#!/usr/bin/env python3
"""
Отдельный файл для выполнения запланированных задач на Render.com
Этот файл будет запускаться как Cron Job на Render каждые 2 недели
"""

import asyncio
import os
import sys
import logging

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import BOT_TOKEN, GITHUB_TOKEN, GITHUB_REPO, ADMIN_IDS, COMPANY_INFO
from database import GitHubDatabase
from report_generator import ReportGenerator
from aiogram import Bot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def send_broadcast():
    """Рассылка активным пользователям"""
    logger.info("Начинаем рассылку...")
    
    db = GitHubDatabase(github_token=GITHUB_TOKEN, repo_name=GITHUB_REPO)
    active_users = db.get_active_users(14)
    user_ids = [user_id for user_id, _ in active_users]
    
    if not user_ids:
        logger.info("Нет активных пользователей")
        return 0, 0
    
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
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
            failed_count += 1
    
    logger.info(f"Рассылка отправлена: {success_count} успешно, {failed_count} ошибок")
    db.record_broadcast(user_ids)
    
    await bot.session.close()
    return success_count, failed_count

async def send_efficiency_report():
    """Отправка отчета эффективности"""
    logger.info("Генерируем отчет эффективности...")
    
    db = GitHubDatabase(github_token=GITHUB_TOKEN, repo_name=GITHUB_REPO)
    report_gen = ReportGenerator(db)
    
    period_id = db.get_current_period_id()
    period_stats = db.get_period_statistics(period_id)
    
    if not period_stats:
        logger.info("Нет данных за текущий период")
        return
    
    report = report_gen.generate_efficiency_report(period_id, period_stats)
    
    bot = Bot(token=BOT_TOKEN)
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=report[:4000]
            )
            logger.info(f"Отчет отправлен администратору {admin_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки отчета администратору {admin_id}: {e}")
    
    await bot.session.close()

async def main():
    """Основная функция"""
    logger.info("Запуск запланированных задач...")
    
    try:
        success, failed = await send_broadcast()
        logger.info(f"Рассылка завершена: {success} успешно, {failed} ошибок")
        
        await send_efficiency_report()
        logger.info("Отчет эффективности отправлен")
        
        logger.info("Все запланированные задачи завершены успешно")
        
    except Exception as e:
        logger.error(f"Ошибка в запланированных задачах: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
