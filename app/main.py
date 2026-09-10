import asyncio
import logging
import os
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, Message
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import (
    check_database_connection,
    create_database_engine,
    initialize_database,
    save_expense,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

dp = Dispatcher()
database_engine: AsyncEngine | None = None

COMMANDS_DESCRIPTION = (
    "Я простий Telegram-бот.\n\n"
    "Доступні команди:\n"
    "/start — отримати це повідомлення\n"
    "/help — переглянути довідку\n"
    "/expense <сума> <Робота|Матеріали> <підкатегорія> — додати витрату"
)

MAIN_CATEGORIES = {
    "робота": "Робота",
    "матеріали": "Матеріали",
}


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    logger.info("received /start command")
    await message.answer(f"Вітаю! Я ваш Telegram-бот.\n\n{COMMANDS_DESCRIPTION}")


@dp.message(Command("help"))
async def help_handler(message: Message) -> None:
    logger.info("received /help command")
    await message.answer(COMMANDS_DESCRIPTION)


@dp.message(Command("expense"))
async def expense_handler(message: Message) -> None:
    """Save an expense sent as /expense <amount> <main category> <subcategory>."""
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) != 4:
        await message.answer(
            "Формат команди: /expense <сума> <Робота|Матеріали> <підкатегорія>\n"
            "Приклад: /expense 2500 Робота Електрика"
        )
        return

    try:
        amount = Decimal(parts[1].replace(",", "."))
    except InvalidOperation:
        await message.answer("Сума має бути числом. Приклад: /expense 2500 Робота Електрика")
        return

    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        await message.answer("Вкажіть додатну суму не більш ніж з двома знаками після коми.")
        return

    main_category_name = MAIN_CATEGORIES.get(parts[2].strip().casefold())
    if main_category_name is None:
        await message.answer("Основна категорія має бути «Робота» або «Матеріали».")
        return

    subcategory_name = parts[3].strip()
    if not subcategory_name or len(subcategory_name) > 100:
        await message.answer("Назва підкатегорії має містити від 1 до 100 символів.")
        return

    if database_engine is None or message.from_user is None:
        logger.error("expense command received before database initialization")
        await message.answer("База даних ще не готова. Спробуйте трохи пізніше.")
        return

    try:
        await save_expense(
            engine=database_engine,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            amount=amount,
            main_category_name=main_category_name,
            subcategory_name=subcategory_name,
        )
    except Exception:
        logger.exception("could not save expense")
        await message.answer("Не вдалося зберегти витрату. Спробуйте ще раз.")
        return

    await message.answer(
        f"Витрату {amount:.2f} грн у категорії «{main_category_name} → {subcategory_name}» збережено."
    )


async def main() -> None:
    global database_engine

    load_dotenv()
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
