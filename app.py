#!/usr/bin/env python3
"""
Упрощенная версия для Render.com без планировщика
"""

import asyncio
import os
import logging
from aiohttp import web

from bot_core import start_bot
from config import IS_RENDER

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Создаем веб-сервер для поддержания активности на Render
async def handle_health_check(request):
    """Обработчик health check запросов"""
    return web.Response(text="Bot is running")

async def create_app():
    """Создание приложения aiohttp"""
    app = web.Application()
    app.router.add_get('/', handle_health_check)
    app.router.add_get('/health', handle_health_check)
    return app

async def run_all():
    """Запуск всех компонентов"""
    # Запускаем веб-сервер
    app = await create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"Веб-сервер запущен на порту {port}")
    
    # Запускаем бота
    await start_bot()

def main():
    """Главная функция"""
    logger.info("Запуск приложения...")
    
    if IS_RENDER:
        logger.info("Работаем на Render.com")
        asyncio.run(run_all())
    else:
        logger.info("Локальный запуск")
        asyncio.run(start_bot())

if __name__ == "__main__":
    main()
