#!/usr/bin/env python3
"""
Минимальная версия приложения для Render.com
"""

import asyncio
import os
import logging
from aiohttp import web
from bot_core import start_bot

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def handle_health_check(request):
    """Обработчик health check запросов"""
    return web.Response(text="Telegram bot is running")

async def create_app():
    """Создание приложения aiohttp"""
    app = web.Application()
    app.router.add_get('/', handle_health_check)
    app.router.add_get('/health', handle_health_check)
    return app

async def run_all():
    """Запуск всех компонентов"""
    # Запускаем веб-сервер в фоне
    app = await create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"Веб-сервер запущен на порту {port}")
    
    # Запускаем бота (основной поток)
    await start_bot()

def main():
    """Главная функция"""
    logger.info("Запуск Telegram бота для ООО 'Тритика'")
    asyncio.run(run_all())

if __name__ == "__main__":
    main()
