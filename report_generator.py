import json
from datetime import datetime, timedelta
from typing import Dict, Any
import pytz

class ReportGenerator:
    def __init__(self, db):
        self.db = db
    
    def generate_efficiency_report(self, period_id: str, period_stats: Dict) -> str:
        """Генерация отчета эффективности"""
        period_start = datetime.fromisoformat(period_stats["start_date"])
        period_end = datetime.fromisoformat(period_stats["end_date"])
        
        total_stats = self.db.stats_data["total"]
        activity_metrics = self.db.calculate_activity_metrics(14)
        
        report = f"📊 ОТЧЕТ ЭФФЕКТИВНОСТИ\n"
        report += f"Период: {period_start.strftime('%d.%m.%Y')} - {period_end.strftime('%d.%m.%Y')}\n"
        report += f"ID периода: {period_id}\n"
        report += "=" * 50 + "\n\n"
        
        report += "📈 СТАТИСТИКА ЗА ПЕРИОД:\n"
        report += f"• Новых пользователей: {period_stats.get('registered', 0)}\n"
        report += f"• Заполненных анкет: {period_stats.get('questionnaires', 0)}\n"
        report += f"• Отправлено рассылок: {period_stats.get('broadcasts_sent', 0)}\n"
        report += f"• Получено сообщений: {period_stats.get('messages_received', 0)}\n"
        report += f"• Получено отзывов: {period_stats.get('feedback_received', 0)}\n"
        report += f"• Активных пользователей: {period_stats.get('active_users', 0)}\n\n"
        
        report += "📊 ОБЩАЯ СТАТИСТИКА:\n"
        report += f"• Всего пользователей: {total_stats.get('registered', 0)}\n"
        report += f"• Всего анкет: {total_stats.get('questionnaires', 0)}\n"
        report += f"• Всего рассылок: {total_stats.get('broadcasts_sent', 0)}\n"
        report += f"• Всего сообщений: {total_stats.get('messages_received', 0)}\n"
        report += f"• Всего отзывов: {total_stats.get('feedback_received', 0)}\n\n"
        
        report += "🎯 МЕТРИКИ ЭФФЕКТИВНОСТИ:\n"
        
        if period_stats.get('registered', 0) > 0:
            questionnaire_rate = (period_stats.get('questionnaires', 0) / period_stats.get('registered', 0)) * 100
            report += f"• Конверсия в анкеты: {questionnaire_rate:.1f}%\n"
        
        report += f"• Активность пользователей: {activity_metrics['activity_rate']:.1f}%\n"
        
        if period_stats.get('questionnaires', 0) > 0:
            feedback_rate = (period_stats.get('feedback_received', 0) / period_stats.get('questionnaires', 0)) * 100
            report += f"• Конверсия в отзывы: {feedback_rate:.1f}%\n"
        
        report += f"• Среднее сообщений на пользователя: {activity_metrics['avg_messages_per_user']:.1f}\n"
        report += f"• Среднее анкет на пользователя: {activity_metrics['avg_questionnaires_per_user']:.1f}\n\n"
        
        report += "📝 АНАЛИЗ И РЕКОМЕНДАЦИИ:\n"
        
        if period_stats.get('registered', 0) > 10 and questionnaire_rate < 30:
            report += "⚠️  Низкая конверсия в анкеты. Рекомендации:\n"
            report += "   - Улучшить процесс заполнения анкеты\n"
            report += "   - Добавить стимулы для заполнения\n"
            report += "   - Упростить форму анкеты\n\n"
        else:
            report += "✅ Конверсия в анкеты на хорошем уровне\n\n"
        
        if activity_metrics['activity_rate'] < 30:
            report += "⚠️  Низкая активность пользователей. Рекомендации:\n"
            report += "   - Увеличить частоту полезного контента\n"
            report += "   - Внедрить систему напоминаний\n"
            report += "   - Добавить интерактивные функции\n\n"
        else:
            report += "✅ Активность пользователей на хорошем уровне\n\n"
        
        if period_stats.get('questionnaires', 0) > 5 and feedback_rate < 20:
            report += "⚠️  Мало обратной связи. Рекомендации:\n"
            report += "   - Внедрить систему поощрений за отзывы\n"
            report += "   - Упростить процесс оставления отзывов\n"
            report += "   - Активнее запрашивать обратную связь\n\n"
        else:
            report += "✅ Уровень обратной связи удовлетворительный\n\n"
        
        report += "=" * 50 + "\n"
        report += "Отчет сгенерирован: " + datetime.now(pytz.UTC).strftime("%d.%m.%Y %H:%M")
        
        return report
