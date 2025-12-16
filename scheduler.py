#!/usr/bin/env python3
"""
Отдельный файл для выполнения запланированных задач на Render.com
Этот файл будет запускаться как Cron Job на Render каждые 2 недели
"""

import asyncio
import os
import sys
import logging

# Добавляем текущую директорию в путь для импортов
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import BOT_TOKEN, GITHUB_TOKEN, GITHUB_REPO, ADMIN_IDS, COMPANY_INFO
from database import GitHubDatabase
from report_generator import ReportGenerator
from aiogram import Bot

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def send_broadcast():
    """Рассылка активным пользователям"""
    logger.info("Starting broadcast...")
    
    # Инициализация базы данных
    db = GitHubDatabase(github_token=GITHUB_TOKEN, repo_name=GITHUB_REPO)
    
    # Получаем активных пользователей
    active_users = db.get_active_users(14)
    user_ids = [user_id for user_id, _ in active_users]
    
    if not user_ids:
        logger.info("No active users found")
        return 0, 0
    
    # Инициализация бота
    bot = Bot(token=BOT_TOKEN)
    
    success_count = 0
    failed_count = 0
    
    for user_id, user_data in active_users:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"📢 Информация от ООО \"Тритика\"\n\n{COMPANY_INFO}"
            )
            success_count += 1
            
            # Небольшая задержка чтобы не превысить лимиты Telegram
            await asyncio.sleep(0.1)
            
        except Exception as e:
            logger.error(f"Error sending to user {user_id}: {e}")
            failed_count += 1
    
    logger.info(f"Broadcast sent: {success_count} success, {failed_count} failed")
    
    # Записываем статистику рассылки
    db.record_broadcast(user_ids)
    
    await bot.session.close()
    return success_count, failed_count

async def send_efficiency_report():
    """Отправка отчета эффективности"""
    logger.info("Generating efficiency report...")
    
    # Инициализация базы данных
    db = GitHubDatabase(github_token=GITHUB_TOKEN, repo_name=GITHUB_REPO)
    
    # Генератор отчетов
    report_gen = ReportGenerator(db)
    
    # Текущий период
    period_id = db.get_current_period_id()
    period_stats = db.get_period_statistics(period_id)
    
    if not period_stats:
        logger.info("No data for current period")
        return
    
    # Генерируем отчет
    report = report_gen.generate_efficiency_report(period_id, period_stats)
    
    # Инициализация бота
    bot = Bot(token=BOT_TOKEN)
    
    # Отправляем администраторам
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=report[:4000]  # Ограничение Telegram
            )
            logger.info(f"Report sent to admin {admin_id}")
        except Exception as e:
            logger.error(f"Error sending report to admin {admin_id}: {e}")
    
    await bot.session.close()

async def main():
    """Основная функция"""
    logger.info("Starting scheduled tasks...")
    
    try:
        # Выполняем рассылку
        success, failed = await send_broadcast()
        logger.info(f"Broadcast completed: {success} success, {failed} failed")
        
        # Отправляем отчет
        await send_efficiency_report()
        logger.info("Efficiency report sent")
        
        logger.info("All scheduled tasks completed successfully")
        
    except Exception as e:
        logger.error(f"Error in scheduled tasks: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
