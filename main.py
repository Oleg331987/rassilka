import os
import logging
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Получаем токен
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен!")
    exit(1)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# =========== КОМАНДЫ БОТА ===========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот для поиска тендеров ООО \"Тритика\"\n\n"
        "📋 Доступные команды:\n"
        "/start - Начало работы\n"
        "/help - Помощь\n"
        "/questionnaire - Заполнить анкету\n"
        "/my_data - Мои данные\n\n"
        "🚀 Бот успешно работает на Railway!"
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "🆘 Помощь по боту:\n\n"
        "1. /questionnaire - заполнить анкету из 9 вопросов\n"
        "2. /my_data - посмотреть свои данные\n"
        "3. /feedback - оставить отзыв\n\n"
        "📞 Контакты:\n"
        "ООО \"Тритика\"\n"
        "Телефон: +7 (4922) 223-222"
    )

@dp.message(Command("questionnaire"))
async def cmd_questionnaire(message: types.Message):
    await message.answer(
        "📝 Анкета для поиска тендеров:\n\n"
        "1. Наименование компании\n"
        "2. ИНН\n"
        "3. Контактное лицо\n"
        "4. Телефон\n"
        "5. E-mail\n"
        "6. Сфера деятельности, ОКВЭД\n"
        "7. Отрасль / Ключевые слова\n"
        "8. Сумма контракта\n"
        "9. Регионы исполнения\n\n"
        "Начнем заполнение? Давайте по порядку..."
    )

@dp.message()
async def handle_all_messages(message: types.Message):
    if not message.text.startswith('/'):
        await message.answer(
            "Используйте команды:\n"
            "/start - начало работы\n"
            "/help - помощь\n"
            "/questionnaire - заполнить анкету"
        )

# =========== ВЕБ-СЕРВЕР ДЛЯ HEALTHCHECK ===========
async def handle_health(request):
    """Обработчик health check"""
    return web.Response(text="Bot is running")

async def start_web_server():
    """Запуск веб-сервера"""
    app = web.Application()
    app.router.add_get('/', handle_health)
    app.router.add_get('/health', handle_health)
    
    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"✅ Веб-сервер запущен на порту {port}")
    logger.info(f"✅ Health check доступен по адресу: http://0.0.0.0:{port}/health")
    
    return runner

# =========== ГЛАВНАЯ ФУНКЦИЯ ===========
async def main():
    """Запуск всего приложения"""
    logger.info("🚀 Запуск Telegram бота на Railway...")
    
    try:
        # Запускаем веб-сервер для health check
        web_runner = await start_web_server()
        
        logger.info("🤖 Запускаю Telegram бота...")
        
        # Запускаем бота
        await dp.start_polling(bot)
        
        # После остановки бота
        await web_runner.cleanup()
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        raise

if __name__ == "__main__":
    # Запускаем асинхронное приложение
    asyncio.run(main())
