import json
from datetime import datetime, timedelta
from typing import Dict, Any, List
import pytz
import matplotlib.pyplot as plt
import pandas as pd
from io import BytesIO

class ReportGenerator:
    def __init__(self, db):
        self.db = db
    
    def generate_efficiency_report(self, period_id: str, period_stats: Dict) -> str:
        """Генерация отчета эффективности за период"""
        period_start = datetime.fromisoformat(period_stats["start_date"])
        period_end = datetime.fromisoformat(period_stats["end_date"])
        
        # Рассчитываем метрики
        total_stats = self.db.stats_data["total"]
        activity_metrics = self.db.calculate_activity_metrics(14)
        
        report = f"📊 ОТЧЕТ ЭФФЕКТИВНОСТИ\n"
        report += f"Период: {period_start.strftime('%d.%m.%Y')} - {period_end.strftime('%d.%m.%Y')}\n"
        report += f"ID периода: {period_id}\n"
        report += "=" * 50 + "\n\n"
        
        # Статистика за период
        report += "📈 СТАТИСТИКА ЗА ПЕРИОД:\n"
        report += f"• Новых пользователей: {period_stats.get('registered', 0)}\n"
        report += f"• Заполненных анкет: {period_stats.get('questionnaires', 0)}\n"
        report += f"• Отправлено рассылок: {period_stats.get('broadcasts_sent', 0)}\n"
        report += f"• Получено сообщений: {period_stats.get('messages_received', 0)}\n"
        report += f"• Получено отзывов: {period_stats.get('feedback_received', 0)}\n"
        report += f"• Активных пользователей: {period_stats.get('active_users', 0)}\n\n"
        
        # Общая статистика
        report += "📊 ОБЩАЯ СТАТИСТИКА:\n"
        report += f"• Всего пользователей: {total_stats.get('registered', 0)}\n"
        report += f"• Всего анкет: {total_stats.get('questionnaires', 0)}\n"
        report += f"• Всего рассылок: {total_stats.get('broadcasts_sent', 0)}\n"
        report += f"• Всего сообщений: {total_stats.get('messages_received', 0)}\n"
        report += f"• Всего отзывов: {total_stats.get('feedback_received', 0)}\n\n"
        
        # Метрики эффективности
        report += "🎯 МЕТРИКИ ЭФФЕКТИВНОСТИ:\n"
        
        # Конверсия анкет
        if period_stats.get('registered', 0) > 0:
            questionnaire_rate = (period_stats.get('questionnaires', 0) / period_stats.get('registered', 0)) * 100
            report += f"• Конверсия в анкеты: {questionnaire_rate:.1f}%\n"
        
        # Активность пользователей
        report += f"• Активность пользователей: {activity_metrics['activity_rate']:.1f}%\n"
        
        # Обратная связь
        if period_stats.get('questionnaires', 0) > 0:
            feedback_rate = (period_stats.get('feedback_received', 0) / period_stats.get('questionnaires', 0)) * 100
            report += f"• Конверсия в отзывы: {feedback_rate:.1f}%\n"
        
        # Средние значения
        report += f"• Среднее сообщений на пользователя: {activity_metrics['avg_messages_per_user']:.1f}\n"
        report += f"• Среднее анкет на пользователя: {activity_metrics['avg_questionnaires_per_user']:.1f}\n\n"
        
        # Анализ и рекомендации
        report += "📝 АНАЛИЗ И РЕКОМЕНДАЦИИ:\n"
        
        # Анализ конверсии
        if period_stats.get('registered', 0) > 10 and questionnaire_rate < 30:
            report += "⚠️  Низкая конверсия в анкеты. Рекомендации:\n"
            report += "   - Улучшить процесс заполнения анкеты\n"
            report += "   - Добавить стимулы для заполнения\n"
            report += "   - Упростить форму анкеты\n\n"
        else:
            report += "✅ Конверсия в анкеты на хорошем уровне\n\n"
        
        # Анализ активности
        if activity_metrics['activity_rate'] < 30:
            report += "⚠️  Низкая активность пользователей. Рекомендации:\n"
            report += "   - Увеличить частоту полезного контента\n"
            report += "   - Внедрить систему напоминаний\n"
            report += "   - Добавить интерактивные функции\n\n"
        else:
            report += "✅ Активность пользователей на хорошем уровне\n\n"
        
        # Анализ обратной связи
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
    
    def generate_weekly_report(self) -> str:
        """Генерация еженедельного отчета"""
        # Статистика за последние 7 дней
        week_ago = datetime.now(pytz.UTC) - timedelta(days=7)
        
        new_users_week = 0
        questionnaires_week = 0
        messages_week = 0
        feedback_week = 0
        
        for user_id, user_data in self.db.users_data["users"].items():
            first_seen = datetime.fromisoformat(user_data["first_seen"])
            if first_seen >= week_ago:
                new_users_week += 1
            
            if "questionnaire_completed_at" in user_data:
                completed_at = datetime.fromisoformat(user_data["questionnaire_completed_at"])
                if completed_at >= week_ago:
                    questionnaires_week += 1
            
            # Считаем сообщения за неделю (упрощенно)
            messages_week += user_data.get("messages_count", 0)  # Это общее количество, не за неделю
        
        report = f"📅 ЕЖЕНЕДЕЛЬНЫЙ ОТЧЕТ\n"
        report += f"Период: {week_ago.strftime('%d.%m.%Y')} - {datetime.now(pytz.UTC).strftime('%d.%m.%Y')}\n"
        report += "=" * 50 + "\n\n"
        
        report += "📈 ЗА НЕДЕЛЮ:\n"
        report += f"• Новых пользователей: {new_users_week}\n"
        report += f"• Заполненных анкет: {questionnaires_week}\n"
        report += f"• Активных пользователей: {len(self.db.get_active_users(7))}\n\n"
        
        # Топ активных пользователей
        report += "🏆 ТОП-5 АКТИВНЫХ ПОЛЬЗОВАТЕЛЕЙ:\n"
        all_users = self.db.get_all_users()
        active_users = sorted(all_users, key=lambda x: x[1].get("messages_count", 0), reverse=True)[:5]
        
        for i, (user_id, user_data) in enumerate(active_users, 1):
            report += f"{i}. {user_data['first_name']} (@{user_data.get('username', 'нет')})\n"
            report += f"   Сообщений: {user_data.get('messages_count', 0)}, "
            report += f"Анкет: {user_data.get('questionnaires_completed', 0)}\n"
        
        report += "\n📊 ОБЩАЯ СТАТИСТИКА:\n"
        report += f"• Всего пользователей: {len(self.db.users_data['users'])}\n"
        report += f"• Всего анкет: {self.db.stats_data['total'].get('questionnaires', 0)}\n"
        report += f"• Всего отзывов: {self.db.stats_data['total'].get('feedback_received', 0)}\n\n"
        
        report += "🎯 ЦЕЛИ НА СЛЕДУЮЩУЮ НЕДЕЛЮ:\n"
        report += "• Увеличить конверсию в анкеты на 10%\n"
        report += "• Получить минимум 5 новых отзывов\n"
        report += "• Привлечь 15 новых пользователей\n"
        
        return report
    
    def generate_detailed_report(self) -> str:
        """Генерация подробного отчета"""
        report = "📋 ПОДРОБНЫЙ ОТЧЕТ ЭФФЕКТИВНОСТИ\n"
        report += f"Дата генерации: {datetime.now(pytz.UTC).strftime('%d.%m.%Y %H:%M')}\n"
        report += "=" * 60 + "\n\n"
        
        # Общая статистика
        total_stats = self.db.stats_data["total"]
        report += "📊 ОБЩАЯ СТАТИСТИКА:\n"
        for metric, value in total_stats.items():
            report += f"• {metric.replace('_', ' ').title()}: {value}\n"
        
        report += "\n📈 СТАТИСТИКА ПО ПЕРИОДАМ:\n"
        for period_id, period_data in self.db.get_all_periods().items():
            report += f"\nПериод {period_id}:\n"
            report += f"  С {period_data['start_date'][:10]} по {period_data['end_date'][:10]}\n"
            for metric, value in period_data.items():
                if metric not in ['start_date', 'end_date']:
                    report += f"  • {metric}: {value}\n"
        
        report += "\n👥 СТАТИСТИКА ПОЛЬЗОВАТЕЛЕЙ:\n"
        all_users = self.db.get_all_users()
        
        # Группировка по активности
        active_30 = self.db.get_active_users(30)
        active_14 = self.db.get_active_users(14)
        active_7 = self.db.get_active_users(7)
        
        report += f"• Всего пользователей: {len(all_users)}\n"
        report += f"• Активных за 30 дней: {len(active_30)}\n"
        report += f"• Активных за 14 дней: {len(active_14)}\n"
        report += f"• Активных за 7 дней: {len(active_7)}\n\n"
        
        # Детализация по пользователям
        report += "👤 ДЕТАЛИЗАЦИЯ ПО ПОЛЬЗОВАТЕЛЯМ:\n"
        for i, (user_id, user_data) in enumerate(all_users[:10], 1):  # Первые 10
            report += f"\n{i}. {user_data['first_name']} (@{user_data.get('username', 'нет')})\n"
            report += f"   ID: {user_id}\n"
            report += f"   Регистрация: {user_data['first_seen'][:10]}\n"
            report += f"   Анкет: {user_data.get('questionnaires_completed', 0)}\n"
            report += f"   Сообщений: {user_data.get('messages_count', 0)}\n"
            report += f"   Отзывов: {user_data.get('feedback_count', 0)}\n"
            report += f"   Рассылок получено: {user_data.get('broadcasts_received', 0)}\n"
        
        if len(all_users) > 10:
            report += f"\n... и еще {len(all_users) - 10} пользователей\n"
        
        report += "\n📝 ВЫВОДЫ И РЕКОМЕНДАЦИИ:\n"
        
        # Анализ роста
        periods = self.db.get_all_periods()
        if len(periods) >= 2:
            period_ids = sorted(periods.keys())
            last_period = periods[period_ids[-1]]
            prev_period = periods[period_ids[-2]] if len(period_ids) >= 2 else None
            
            if prev_period:
                growth_rate = ((last_period.get('registered', 0) - prev_period.get('registered', 0)) / 
                              prev_period.get('registered', 0) * 100) if prev_period.get('registered', 0) > 0 else 0
                report += f"• Рост пользователей: {growth_rate:.1f}%\n"
        
        # Рекомендации
        report += "\n🎯 РЕКОМЕНДАЦИИ:\n"
        report += "1. Увеличить частоту полезного контента\n"
        report += "2. Внедрить систему реферальных бонусов\n"
        report += "3. Добавить уведомления о новых тендерах\n"
        report += "4. Улучшить процесс обратной связи\n"
        report += "5. Провести анализ целевой аудитории\n"
        
        return report
    
    def save_report_to_file(self, report: str, filename: str):
        """Сохранение отчета в файл"""
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(report)
            print(f"Отчет сохранен в файл: {filename}")
        except Exception as e:
            print(f"Ошибка сохранения отчета: {e}")
    
    def create_visualization(self):
        """Создание визуализации статистики"""
        try:
            # Подготовка данных
            periods = list(self.db.get_all_periods().keys())
            registered = [self.db.get_period_statistics(p).get('registered', 0) for p in periods]
            questionnaires = [self.db.get_period_statistics(p).get('questionnaires', 0) for p in periods]
            
            # Создание графика
            fig, ax = plt.subplots(figsize=(10, 6))
            x = range(len(periods))
            
            ax.bar(x, registered, label='Новые пользователи', alpha=0.7)
            ax.bar(x, questionnaires, label='Заполненные анкеты', alpha=0.7)
            
            ax.set_xlabel('Периоды')
            ax.set_ylabel('Количество')
            ax.set_title('Статистика по периодам')
            ax.set_xticks(x)
            ax.set_xticklabels(periods, rotation=45)
            ax.legend()
            
            plt.tight_layout()
            
            # Сохранение в буфер
            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100)
            buf.seek(0)
            plt.close()
            
            return buf
        except Exception as e:
            print(f"Ошибка создания визуализации: {e}")
            return None