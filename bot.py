import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот для поиска тендеров.\n\n"
        "📋 Доступные команды:\n"
        "/start - Начало работы\n"
        "/help - Помощь\n"
        "/questionnaire - Заполнить анкету\n\n"
        "Бот успешно работает на Railway! ✅"
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "🆘 Помощь:\n"
        "1. /start - начать работу\n"
        "2. /questionnaire - заполнить анкету\n"
        "3. Бот автоматически сохраняет данные"
    )

async def main():
    logger.info("Бот запущен на Railway!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
