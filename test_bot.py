import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "8227089023:AAFHtDuflB-wKcxp-bEwfPU0AgD1smFyt5I"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("🤖 Бот работает! Тест успешен!")

async def main():
    print("Тестируем бота...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
