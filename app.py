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
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

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

async def scheduled_broadcast():
    """Запланированная рассылка"""
    logger.info("Запуск запланированной рассылки...")
    success, failed = await send_broadcast_to_active_users()
    logger.info(f"Рассылка завершена: {success} успешно, {failed} ошибок")

async def scheduled_report():
    """Запланированный отчет"""
    logger.info("Запуск запланированного отчета...")
    await send_efficiency_report_to_admins()
    logger.info("Отчет отправлен")

async def start_scheduler():
    """Запуск планировщика"""
    scheduler = AsyncIOScheduler(timezone=pytz.UTC)
    
    # Рассылка каждые 2 недели в понедельник в 10:00
    scheduler.add_job(
        scheduled_broadcast,
        CronTrigger(day_of_week='mon', hour=10, minute=0),
        kwargs={}
    )
    
    # Отчет каждые 2 недели в понедельник в 11:00
    scheduler.add_job(
        scheduled_report,
        CronTrigger(day_of_week='mon', hour=11, minute=0),
        kwargs={}
    )
    
    scheduler.start()
    logger.info("Планировщик запущен")
    
    # Бесконечный цикл чтобы планировщик работал
    try:
        while True:
            await asyncio.sleep(3600)  # Спим час
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()

async def run_all():
    """Запуск всех компонентов"""
    # Запускаем планировщик в фоне
    scheduler_task = asyncio.create_task(start_scheduler())
    
    # Запускаем бота
    bot_task = asyncio.create_task(start_bot())
    
    # Запускаем веб-сервер
    app = await create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"Веб-сервер запущен на порту {port}")
    
    # Ждем завершения всех задач
    await asyncio.gather(scheduler_task, bot_task)

def main():
    """Главная функция"""
    logger.info("Запуск приложения...")
    
    if IS_RENDER:
        logger.info("Работаем на Render.com со встроенным планировщиком")
        asyncio.run(run_all())
    else:
        logger.info("Локальный запуск")
        asyncio.run(start_bot())

if __name__ == "__main__":
    main()
