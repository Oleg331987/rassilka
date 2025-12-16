#!/usr/bin/env python3
"""
Главный файл приложения для деплоя на Render.com
Включает веб-сервер для поддержания активности и бота
"""

import asyncio
import os
import logging
from aiohttp import web
import threading
import time

from bot_core import start_bot, send_broadcast_to_active_users, send_efficiency_report_to_admins
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

async def handle_send_broadcast(request):
    """Ручной запуск рассылки (для тестирования)"""
    try:
        success, failed = await send_broadcast_to_active_users()
        return web.Response(text=f"Broadcast sent: {success} success, {failed} failed")
    except Exception as e:
        return web.Response(text=f"Error: {str(e)}")

async def handle_send_report(request):
    """Ручной запуск отчета (для тестирования)"""
    try:
        await send_efficiency_report_to_admins()
        return web.Response(text="Report sent to admins")
    except Exception as e:
        return web.Response(text=f"Error: {str(e)}")

async def create_app():
    """Создание приложения aiohttp"""
    app = web.Application()
    
    # Добавляем роуты
    app.router.add_get('/', handle_health_check)
    app.router.add_get('/health', handle_health_check)
    app.router.add_get('/broadcast', handle_send_broadcast)
    app.router.add_get('/report', handle_send_report)
    
    return app

def run_bot():
    """Запуск бота в отдельном потоке"""
    logger.info("Starting bot...")
    asyncio.run(start_bot())

def run_web_server():
    """Запуск веб-сервера"""
    logger.info("Starting web server...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    app = loop.run_until_complete(create_app())
    
    # Порт берем из переменной окружения или используем 8080
    port = int(os.environ.get("PORT", 8080))
    
    web.run_app(app, port=port, host='0.0.0.0')

def main():
    """Главная функция запуска"""
    logger.info("Starting application...")
    
    if IS_RENDER:
        logger.info("Running on Render.com")
        
        # На Render.com нужно запускать веб-сервер
        # Бот будет запущен внутри веб-сервера
        run_web_server()
    else:
        # Локальный запуск - только бот
        logger.info("Running locally")
        run_bot()

if __name__ == "__main__":
    main()
