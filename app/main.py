import asyncio
import logging
import os
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import (
    check_database_connection,
    create_web_login_code,
    create_database_engine,
    delete_user_transaction,
    get_user_transaction,
    get_user_transactions,
    initialize_database,
    save_transaction,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

dp = Dispatcher(storage=MemoryStorage())
database_engine: AsyncEngine | None = None

COMMANDS_DESCRIPTION = (
    "Я допоможу зберігати фінансові операції ремонту.\n\n"
    "Доступні команди:\n"
    "/add — додати дохід або витрату покроково\n"
    "/expense — додати операцію покроково\n"
    "/id — показати ваш Telegram ID\n"
    "/login — отримати одноразовий код для входу у web dashboard\n"
    "/transactions — показати останні операції\n"
    "/delete <ID> — видалити операцію\n"
    "/cancel — скасувати поточне введення\n"
    "/help — переглянути довідку"
)

MAIN_CATEGORIES = {
    "робота": "Роботи",
    "роботи": "Роботи",
    "матеріали": "Матеріали",
}

TRANSACTION_TYPES = {
    "витрата": ("expense", "Витрата"),
    "дохід": ("income", "Дохід"),
}


class TransactionForm(StatesGroup):
    transaction_type = State()
    amount = State()
    exchange_rate = State()
    main_category = State()
    subcategory = State()
    description = State()


class DeleteTransactionForm(StatesGroup):
    confirmation = State()


def transaction_type_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Витрата"), KeyboardButton(text="Дохід")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def main_category_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Роботи"), KeyboardButton(text="Матеріали")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def delete_confirmation_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Так, видалити"), KeyboardButton(text="Скасувати")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def transaction_type_label(transaction_type: str) -> str:
    return "Дохід" if transaction_type == "income" else "Витрата"


def transaction_details(transaction: dict[str, object]) -> str:
    usd_amount = transaction.get("amount_usd")
    exchange_rate = transaction.get("exchange_rate")
    usd_details = (
        f"\n${Decimal(str(usd_amount)):.2f} (курс {Decimal(str(exchange_rate)):.4f} грн/USD)"
        if usd_amount is not None and exchange_rate is not None
        else ""
    )
    return (
        f"#{transaction['id']} — {transaction_type_label(str(transaction['transaction_type']))}\n"
        f"{transaction.get('main_category') or 'Категорія'} → {transaction['subcategory']} → "
        f"{transaction.get('description') or 'Без позиції'}\n"
        f"{Decimal(str(transaction['amount'])):.2f} грн{usd_details}"
    )


async def send_web_login_code(message: Message) -> None:
    """Create and send a dashboard login code to the Telegram user."""
    if database_engine is None or message.from_user is None:
        await message.answer("База даних ще не готова. Спробуйте трохи пізніше.")
        return

    try:
        code = await create_web_login_code(database_engine, message.from_user.id)
    except Exception:
        logger.exception("could not create web login code")
        await message.answer("Не вдалося створити код входу. Спробуйте ще раз.")
        return

    await message.answer(
        f"Ваш код для входу у web dashboard: {code}\n"
        f"Ваш Telegram ID: {message.from_user.id}\n\n"
        "Введіть обидва значення на сайті. Код дійсний 5 хвилин, одноразовий і має не більше 5 спроб введення."
    )


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    payload = (message.text or "").partition(" ")[2].strip().casefold()
    if payload == "login":
        logger.info("received Telegram login deep link")
        await send_web_login_code(message)
        return

    logger.info("received /start command")
    await message.answer(f"Вітаю!\n\n{COMMANDS_DESCRIPTION}")


@dp.message(Command("help"))
async def help_handler(message: Message) -> None:
    logger.info("received /help command")
    await message.answer(COMMANDS_DESCRIPTION)


@dp.message(Command("id"))
async def telegram_id_handler(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Не вдалося визначити ваш Telegram ID.")
        return

    await message.answer(f"Ваш Telegram ID: {message.from_user.id}\nВведіть його у web dashboard для перегляду своїх даних.")


@dp.message(Command("login"))
async def login_handler(message: Message) -> None:
    """Send a short-lived one-time code for the web dashboard."""
    await send_web_login_code(message)


@dp.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Наразі немає операції для скасування.", reply_markup=ReplyKeyboardRemove())
        return

    await state.clear()
    await message.answer("Введення операції скасовано.", reply_markup=ReplyKeyboardRemove())


@dp.message(Command("add", "expense"))
async def start_transaction_handler(message: Message, state: FSMContext) -> None:
    """Start a guided transaction form."""
    await state.clear()
    await state.set_state(TransactionForm.transaction_type)
    await message.answer("Оберіть тип операції: витрата чи дохід?", reply_markup=transaction_type_keyboard())


@dp.message(Command("transactions"))
async def transactions_handler(message: Message) -> None:
    if database_engine is None or message.from_user is None:
        await message.answer("База даних ще не готова. Спробуйте трохи пізніше.")
        return

    try:
        transactions = await get_user_transactions(database_engine, message.from_user.id)
    except Exception:
        logger.exception("could not get user transactions")
        await message.answer("Не вдалося отримати операції. Спробуйте ще раз.")
        return

    if not transactions:
        await message.answer("У вас поки немає збережених операцій.")
        return

    details = "\n\n".join(transaction_details(transaction) for transaction in transactions)
    await message.answer(f"Останні операції:\n\n{details}\n\nДля видалення: /delete <ID>")


@dp.message(Command("delete"))
async def delete_transaction_handler(message: Message, state: FSMContext) -> None:
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат команди: /delete <ID>. Спочатку перегляньте ID через /transactions.")
        return

    if database_engine is None or message.from_user is None:
        await message.answer("База даних ще не готова. Спробуйте трохи пізніше.")
        return

    transaction_id = int(parts[1])
    try:
        transaction = await get_user_transaction(database_engine, message.from_user.id, transaction_id)
    except Exception:
        logger.exception("could not get transaction for deletion")
        await message.answer("Не вдалося знайти операцію. Спробуйте ще раз.")
        return

    if transaction is None:
        await message.answer("Операцію не знайдено або вона не належить вам.")
        return

    await state.clear()
    await state.update_data(transaction_id=transaction_id)
    await state.set_state(DeleteTransactionForm.confirmation)
    await message.answer(
        f"Видалити цю операцію?\n\n{transaction_details(transaction)}\n\nЦю дію не можна скасувати.",
        reply_markup=delete_confirmation_keyboard(),
    )


@dp.message(TransactionForm.transaction_type)
async def transaction_type_handler(message: Message, state: FSMContext) -> None:
    selected_type = TRANSACTION_TYPES.get((message.text or "").strip().casefold())
    if selected_type is None:
        await message.answer("Оберіть «Витрата» або «Дохід» кнопкою нижче.", reply_markup=transaction_type_keyboard())
        return

    transaction_type, transaction_type_label = selected_type
    await state.update_data(transaction_type=transaction_type, transaction_type_label=transaction_type_label)
    await state.set_state(TransactionForm.amount)
    await message.answer("Вкажіть суму:", reply_markup=ReplyKeyboardRemove())


@dp.message(TransactionForm.amount)
async def amount_handler(message: Message, state: FSMContext) -> None:
    try:
        amount = Decimal((message.text or "").replace(",", ".").strip())
    except InvalidOperation:
        await message.answer("Сума має бути числом. Наприклад: 2500 або 2500.50")
        return

    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        await message.answer("Вкажіть додатну суму не більш ніж з двома знаками після коми.")
        return

    await state.update_data(amount=str(amount))
    await state.set_state(TransactionForm.exchange_rate)
    await message.answer("Вкажіть курс USD у гривнях на дату операції. Наприклад: 41.50")


@dp.message(TransactionForm.exchange_rate)
async def exchange_rate_handler(message: Message, state: FSMContext) -> None:
    try:
        exchange_rate = Decimal((message.text or "").replace(",", ".").strip())
    except InvalidOperation:
        await message.answer("Курс має бути числом. Наприклад: 41.50")
        return

    if not exchange_rate.is_finite() or exchange_rate <= 0 or exchange_rate.as_tuple().exponent < -4:
        await message.answer("Вкажіть додатний курс не більш ніж з чотирма знаками після коми.")
        return

    await state.update_data(exchange_rate=str(exchange_rate))
    await state.set_state(TransactionForm.main_category)
    await message.answer("Оберіть основну категорію: Роботи чи Матеріали?", reply_markup=main_category_keyboard())


@dp.message(TransactionForm.main_category)
async def main_category_handler(message: Message, state: FSMContext) -> None:
    main_category_name = MAIN_CATEGORIES.get((message.text or "").strip().casefold())
    if main_category_name is None:
        await message.answer("Оберіть «Роботи» або «Матеріали» кнопкою нижче.", reply_markup=main_category_keyboard())
        return

    await state.update_data(main_category_name=main_category_name)
    await state.set_state(TransactionForm.subcategory)
    await message.answer("Вкажіть підкатегорію. Наприклад: Електрика, Сантехніка або Малярка.", reply_markup=ReplyKeyboardRemove())


@dp.message(TransactionForm.subcategory)
async def subcategory_handler(message: Message, state: FSMContext) -> None:
    subcategory_name = (message.text or "").strip()
    if not subcategory_name or len(subcategory_name) > 100:
        await message.answer("Назва підкатегорії має містити від 1 до 100 символів.")
        return

    await state.update_data(subcategory_name=subcategory_name)
    await state.set_state(TransactionForm.description)
    await message.answer("Вкажіть конкретну позицію. Наприклад: Ванна, труба PPR 25 мм або 2 мішка штукатурки.")


@dp.message(TransactionForm.description)
async def description_handler(message: Message, state: FSMContext) -> None:
    description = (message.text or "").strip()
    if not description or len(description) > 255:
        await message.answer("Конкретна позиція має містити від 1 до 255 символів.")
        return

    if database_engine is None or message.from_user is None:
        logger.error("transaction received before database initialization")
        await message.answer("База даних ще не готова. Спробуйте трохи пізніше.")
        return

    data = await state.get_data()
    amount = Decimal(data["amount"])
    exchange_rate = Decimal(data["exchange_rate"])
    try:
        transaction = await save_transaction(
            engine=database_engine,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            amount=amount,
            exchange_rate=exchange_rate,
            main_category_name=data["main_category_name"],
            subcategory_name=data["subcategory_name"],
            description=description,
            transaction_type=data["transaction_type"],
        )
    except Exception:
        logger.exception("could not save transaction")
        await message.answer("Не вдалося зберегти операцію. Спробуйте ще раз.")
        return

    await state.clear()
    await message.answer(
        f"{data['transaction_type_label']} {amount:.2f} грн (${Decimal(str(transaction['amount_usd'])):.2f}, курс {exchange_rate:.4f}) збережено:\n"
        f"{data['main_category_name']} → {data['subcategory_name']} → {description}",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(DeleteTransactionForm.confirmation)
async def delete_confirmation_handler(message: Message, state: FSMContext) -> None:
    answer = (message.text or "").strip().casefold()
    if answer == "скасувати":
        await state.clear()
        await message.answer("Видалення скасовано.", reply_markup=ReplyKeyboardRemove())
        return

    if answer != "так, видалити":
        await message.answer("Оберіть «Так, видалити» або «Скасувати» кнопкою нижче.", reply_markup=delete_confirmation_keyboard())
        return

    if database_engine is None or message.from_user is None:
        await message.answer("База даних ще не готова. Спробуйте трохи пізніше.")
        return

    data = await state.get_data()
    try:
        deleted = await delete_user_transaction(
            database_engine,
            message.from_user.id,
            data["transaction_id"],
        )
    except Exception:
        logger.exception("could not delete transaction")
        await message.answer("Не вдалося видалити операцію. Спробуйте ще раз.")
        return

    await state.clear()
    if deleted:
        await message.answer("Операцію видалено.", reply_markup=ReplyKeyboardRemove())
    else:
        await message.answer("Операцію вже видалено або її не знайдено.", reply_markup=ReplyKeyboardRemove())


async def main() -> None:
    global database_engine

    load_dotenv(override=True)
    token = os.getenv("BOT_TOKEN")

    if not token:
        raise RuntimeError("Змінна середовища BOT_TOKEN не встановлена.")

    database_engine = create_database_engine()
    await check_database_connection(database_engine)
    await initialize_database(database_engine)
    logger.info("database connection verified")

    bot = Bot(token=token)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Отримати привітання"),
            BotCommand(command="add", description="Додати дохід або витрату"),
            BotCommand(command="expense", description="Додати операцію"),
            BotCommand(command="id", description="Показати мій Telegram ID"),
            BotCommand(command="login", description="Отримати код для web dashboard"),
            BotCommand(command="transactions", description="Показати останні операції"),
            BotCommand(command="delete", description="Видалити операцію за ID"),
            BotCommand(command="cancel", description="Скасувати введення"),
            BotCommand(command="help", description="Переглянути довідку"),
        ]
    )
    logger.info("bot started")
    try:
        await dp.start_polling(bot)
    finally:
        await database_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
