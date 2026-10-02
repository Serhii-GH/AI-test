"""Import the supplied renovation workbook into one Telegram user's ledger.

Run with ``--dry-run`` first. The importer reads the original UAH amount and
USD rate, derives USD via the app's transaction storage, and skips records
already present with the same amount, rate, and position.
"""

import argparse
import asyncio
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

from app.database import (
    create_database_engine,
    get_transactions,
    initialize_database,
    save_transaction,
)


MAIN_NAMESPACE = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
EXCEL_EPOCH = date(1899, 12, 30)


@dataclass
class RepairRecord:
    row_number: int
    amount: Decimal
    exchange_rate: Decimal
    position: str
    operation_date: date | None
    category: str = ""
    subcategory: str = ""


def _text_content(node: ElementTree.Element) -> str:
    return "".join(text or "" for text in node.itertext())


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [_text_content(item) for item in root.findall(f"{MAIN_NAMESPACE}si")]


def _cell_column(reference: str) -> str:
    return re.sub(r"\d+", "", reference)


def read_workbook(workbook_path: Path) -> list[RepairRecord]:
    with zipfile.ZipFile(workbook_path) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))

    records: list[RepairRecord] = []
    for row in sheet.findall(f".//{MAIN_NAMESPACE}sheetData/{MAIN_NAMESPACE}row"):
        values: dict[str, str] = {}
        for cell in row.findall(f"{MAIN_NAMESPACE}c"):
            value_node = cell.find(f"{MAIN_NAMESPACE}v")
            value = value_node.text if value_node is not None and value_node.text else ""
            if cell.get("t") == "s" and value:
                value = shared_strings[int(value)]
            elif cell.get("t") == "inlineStr":
                inline_node = cell.find(f"{MAIN_NAMESPACE}is")
                value = _text_content(inline_node) if inline_node is not None else ""
            values[_cell_column(cell.get("r", ""))] = value.strip()

        position = values.get("D", "")
        if not position or position == "Роботи і матеріали" or position == "ЗАГАЛОМ":
            continue
        if not values.get("B") or not values.get("C"):
            continue

        excel_date = values.get("E")
        operation_date = (
            EXCEL_EPOCH + timedelta(days=int(float(excel_date)))
            if excel_date
            else None
        )
        records.append(
            RepairRecord(
                row_number=int(row.get("r", "0")),
                amount=Decimal(values["B"]),
                exchange_rate=Decimal(values["C"]),
                position=position,
                operation_date=operation_date,
            )
        )
    return records


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def classify(position: str) -> tuple[str, str]:
    """Map the signed workbook item to the agreed category hierarchy."""
    value = _normalized(position)

    agreed_categories = {
        "вивіз сміття+генератор": ("Роботи", "Демонтаж/вивіз"),
        "робота плиточника + деякі матеріали": ("Роботи", "Плитка"),
        "ремонт будинку": ("Роботи", "Інші роботи"),
        "точки освітлення + лампочки": ("Матеріали", "Освітлення"),
        "меблі робота + ледки": ("Роботи", "Меблі"),
        "двері декоративка": ("Роботи", "Стіни/декоративне покриття"),
        "робота меблі + дзеркала + дрібниці": ("Роботи", "Меблі/декор"),
    }
    if value in agreed_categories:
        return agreed_categories[value]

    work_rules = (
        (("демонтаж", "вивіз демонтован", "перестінка"), "Стіни/демонтаж"),
        (("робота сантехніка", "сантехнік робота"), "Сантехніка"),
        (("робота електрика", "монтаж підрозетників"), "Електрика"),
        (("заливка стяжки", "робота підлога", "монтаж кварцвінілу"), "Підлога"),
        (("заміри двері",), "Двері"),
        (("підготовка стін",), "Стіни"),
        (("стеля",), "Стеля"),
        (("дрібні роботи",), "Інші роботи"),
        (("робота меблі",), "Меблі"),
        (("профілактика вікон",), "Вікна"),
    )
    for keywords, subcategory in work_rules:
        if any(keyword in value for keyword in keywords):
            return "Роботи", subcategory

    material_rules = (
        (("змішувач", "сантехніка", "трап", "умивальник", "бойлер", "унітаз", "скло в душ"), "Сантехніка"),
        (("батарея", "терморегулятор", "рушникосушка"), "Опалення"),
        (("матеріали для стіни", "штукатурка", "декоративка баранець"), "Стіни"),
        (("декоративка", "декор"), "Декор"),
        (("матеріали електрика", "розетки", "люстра"), "Електрика/освітлення"),
        (("духовка", "мікрохвильова", "холодильник", "пральна", "посудомийка", "телевізор"), "Техніка"),
        (("траса кондиціонера", "рекуператор"), "Вентиляція/кондиціонування"),
        (("стяжка", "клей", "spc", "плитка", "підлоги", "тепла підлога", "кварцвініл", "плінтуси"), "Підлога"),
        (("двері", "дверні ручки"), "Двері"),
        (("вікна", "ролети"), "Вікна"),
        (("мішки", "віник"), "Витратні матеріали"),
        (("ліжко", "стіл", "меблі", "фурнітура", "матрац", "диван", "віяр", "столик", "стільці"), "Меблі"),
        (("штори", "тюль", "жалюзі", "коврик", "пуф", "подушка"), "Текстиль/декор"),
        (("витяжка", "варочна панель"), "Кухонна техніка"),
        (("дзеркала",), "Меблі/декор"),
        (("карніз",), "Текстиль/декор"),
    )
    for keywords, subcategory in material_rules:
        if any(keyword in value for keyword in keywords):
            return "Матеріали", subcategory

    raise ValueError(f"No category rule for Excel row position: {position!r}")


def fill_missing_dates(records: list[RepairRecord]) -> None:
    """Use the agreed midpoint rule, then two-day intervals after the last date."""
    dated_indexes = [index for index, record in enumerate(records) if record.operation_date]
    if not dated_indexes:
        raise ValueError("The workbook has no dates to use as import anchors.")

    last_dated_index = dated_indexes[-1]
    for index, record in enumerate(records):
        if record.operation_date:
            continue

        previous_indexes = [dated_index for dated_index in dated_indexes if dated_index < index]
        next_indexes = [dated_index for dated_index in dated_indexes if dated_index > index]
        if next_indexes:
            previous_date = records[previous_indexes[-1]].operation_date
            next_date = records[next_indexes[0]].operation_date
            assert previous_date is not None and next_date is not None
            record.operation_date = previous_date + (next_date - previous_date) // 2
        else:
            last_date = records[last_dated_index].operation_date
            assert last_date is not None
            record.operation_date = last_date + timedelta(days=2 * (index - last_dated_index))


def _fingerprint(amount: Decimal, exchange_rate: Decimal, position: str) -> tuple[Decimal, Decimal, str]:
    return amount.quantize(Decimal("0.01")), exchange_rate.quantize(Decimal("0.0001")), _normalized(position)


async def import_records(workbook_path: Path, telegram_id: int, apply_changes: bool) -> None:
    records = read_workbook(workbook_path)
    fill_missing_dates(records)
    for record in records:
        record.category, record.subcategory = classify(record.position)

    engine = create_database_engine()
    try:
        await initialize_database(engine)
        existing_transactions = await get_transactions(engine, telegram_id)
        existing_fingerprints = {
            _fingerprint(
                Decimal(str(transaction["amount"])),
                Decimal(str(transaction["exchange_rate"])),
                str(transaction.get("description") or ""),
            )
            for transaction in existing_transactions
            if transaction.get("exchange_rate") is not None
        }
        new_records = [
            record
            for record in records
            if _fingerprint(record.amount, record.exchange_rate, record.position) not in existing_fingerprints
        ]
        print(f"Workbook records: {len(records)}")
        print(f"Already present and skipped: {len(records) - len(new_records)}")
        print(f"Ready to import: {len(new_records)}")
        if not apply_changes:
            return

        for record in new_records:
            assert record.operation_date is not None
            await save_transaction(
                engine=engine,
                telegram_id=telegram_id,
                username=None,
                amount=record.amount,
                exchange_rate=record.exchange_rate,
                main_category_name=record.category,
                subcategory_name=record.subcategory,
                description=record.position,
                transaction_type="expense",
                created_at=datetime.combine(record.operation_date, time.min, tzinfo=timezone.utc),
            )
        print(f"Imported: {len(new_records)}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--telegram-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(import_records(arguments.file, arguments.telegram_id, arguments.apply))


if __name__ == "__main__":
    main()
