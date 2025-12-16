from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
import pytz
from config import COMPANY_INFO
import asyncio

class BroadcastScheduler:
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC)
    
    def start(self):
        """Запуск планировщика задач"""
        # Рассылка каждые 2 недели в понедельник в 10:00
        self.scheduler.add_job(
            self.send_broadcast,
            CronTrigger(day_of_week='mon', hour=10, minute=0),
            kwargs={'days': 14}
        )
        
        # Отчет эффективности каждые 2 недели в понедельник в 11:00
        self.scheduler.add_job(
            self.send_efficiency_report,
            CronTrigger(day_of_week='mon', hour=11, minute=0)
        )
        
        # Ежедневная проверка активности
        self.scheduler.add_job(
            self.check_inactive_users,
            CronTrigger(hour=9, minute=0)
        )
        
        # Еженедельный отчет активности (каждую пятницу)
        self.scheduler.add_job(
            self.send_weekly_report,
            CronTrigger(day_of_week='fri', hour=16, minute=0)
        )
        
        self.scheduler.start()
        print("Планировщик запущен")
    
    async def send_broadcast(self, days: int = 14):
        """Рассылка информации активным пользователям"""
        active_users = self.db.get_active_users(days)
        user_ids = [user_id for user_id, _ in active_users]
        
        success_count = 0
        failed_count = 0
        
        for user_id, user_data in active_users:
            try:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=f"📢 Информация от ООО \"Тритика\"\n\n{COMPANY_INFO}"
                )
                success_count += 1
                
                # Небольшая задержка чтобы не превысить лимиты Telegram
                await asyncio.sleep(0.1)
                
            except Exception as e:
                print(f"Ошибка отправки пользователю {user_id}: {e}")
                failed_count += 1
        
        print(f"Рассылка отправлена: {success_count} успешно, {failed_count} ошибок")
        
        # Записываем статистику рассылки
        self.db.record_broadcast(user_ids)
        
        # Обновляем время последней рассылки
        self.db.users_data["last_broadcast"] = datetime.now(pytz.UTC).isoformat()
        self.db.save_users()
    
    async def send_efficiency_report(self):
        """Отправка отчета эффективности за период"""
        from report_generator import ReportGenerator
        report_gen = ReportGenerator(self.db)
        
        # Получаем статистику за текущий период
        period_id = self.db.get_current_period_id()
        period_stats = self.db.get_period_statistics(period_id)
        
        if not period_stats:
            print("Нет данных для отчета за текущий период")
            return
        
        # Генерируем отчет
        report = report_gen.generate_efficiency_report(period_id, period_stats)
        
        # Отправляем администраторам
        from config import ADMIN_IDS
        for admin_id in ADMIN_IDS:
            try:
                await self.bot.send_message(
                    chat_id=admin_id,
                    text=report
                )
                print(f"Отчет эффективности отправлен администратору {admin_id}")
            except Exception as e:
                print(f"Ошибка отправки отчета администратору {admin_id}: {e}")
        
        # Генерируем и сохраняем подробный отчет
        detailed_report = report_gen.generate_detailed_report()
        
        # Сохраняем отчет в файл
        report_gen.save_report_to_file(detailed_report, f"efficiency_report_{period_id}.txt")
    
    async def send_weekly_report(self):
        """Отправка еженедельного отчета"""
        from report_generator import ReportGenerator
        report_gen = ReportGenerator(self.db)
        
        # Генерируем недельный отчет
        report = report_gen.generate_weekly_report()
        
        # Отправляем администраторам
        from config import ADMIN_IDS
        for admin_id in ADMIN_IDS:
            try:
                await self.bot.send_message(
                    chat_id=admin_id,
                    text=report[:4000]  # Ограничение Telegram
                )
                print(f"Еженедельный отчет отправлен администратору {admin_id}")
            except Exception as e:
                print(f"Ошибка отправки еженедельного отчета администратору {admin_id}: {e}")
    
    async def check_inactive_users(self):
        """Пометка неактивных пользователей"""
        cutoff_date = datetime.now(pytz.UTC) - timedelta(days=90)  # 3 месяца
        
        for user_id, user_data in self.db.users_data["users"].items():
            last_activity = datetime.fromisoformat(user_data["last_activity"])
            if last_activity < cutoff_date:
                user_data["active"] = False
        
        self.db.save_users()
        print(f"Проверка неактивных пользователей завершена")