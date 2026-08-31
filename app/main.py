import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, Message
from dotenv import load_dotenv


dp = Dispatcher()

COMMANDS_DESCRIPTION = (
    "Я простий Telegram-бот.\n\n"
    "Доступні команди:\n"
    "/start — отримати це повідомлення\n"
    "/help — переглянути довідку"
)


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer(f"Вітаю! Я ваш Telegram-бот.\n\n{COMMANDS_DESCRIPTION}")


@dp.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(COMMANDS_DESCRIPTION)


async def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")

    if not token:
        raise RuntimeError("Змінна середовища BOT_TOKEN не встановлена.")

    bot = Bot(token=token)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Отримати привітання"),
            BotCommand(command="help", description="Переглянути довідку"),
        ]
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
