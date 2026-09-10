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
    create_database_engine,
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
    "/cancel — скасувати поточне введення\n"
    "/help — переглянути довідку"
)

MAIN_CATEGORIES = {
    "робота": "Робота",
    "матеріали": "Матеріали",
}

TRANSACTION_TYPES = {
    "витрата": ("expense", "Витрата"),
    "дохід": ("income", "Дохід"),
}


class TransactionForm(StatesGroup):
    transaction_type = State()
    amount = State()
    main_category = State()
    subcategory = State()
    description = State()


def transaction_type_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Витрата"), KeyboardButton(text="Дохід")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def main_category_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Робота"), KeyboardButton(text="Матеріали")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    logger.info("received /start command")
    await message.answer(f"Вітаю!\n\n{COMMANDS_DESCRIPTION}")


@dp.message(Command("help"))
async def help_handler(message: Message) -> None:
    logger.info("received /help command")
    await message.answer(COMMANDS_DESCRIPTION)


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
    await state.set_state(TransactionForm.main_category)
    await message.answer("Оберіть основну категорію: Робота чи Матеріали?", reply_markup=main_category_keyboard())


@dp.message(TransactionForm.main_category)
async def main_category_handler(message: Message, state: FSMContext) -> None:
    main_category_name = MAIN_CATEGORIES.get((message.text or "").strip().casefold())
    if main_category_name is None:
        await message.answer("Оберіть «Робота» або «Матеріали» кнопкою нижче.", reply_markup=main_category_keyboard())
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
    try:
        await save_transaction(
            engine=database_engine,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            amount=amount,
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
        f"{data['transaction_type_label']} {amount:.2f} грн збережено:\n"
        f"{data['main_category_name']} → {data['subcategory_name']} → {description}",
        reply_markup=ReplyKeyboardRemove(),
    )


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
