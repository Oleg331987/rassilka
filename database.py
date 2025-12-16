import json
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import pytz

class SimpleDatabase:
    def __init__(self):
        self.users_file = "users.json"
        self.stats_file = "statistics.json"
        self.users_data = self.load_users()
        self.stats_data = self.load_statistics()
    
    def load_users(self) -> Dict:
        """Загрузка данных пользователей"""
        try:
            if os.path.exists(self.users_file):
                with open(self.users_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {"users": {}, "last_broadcast": None}
    
    def save_users(self) -> None:
        """Сохранение данных пользователей"""
        try:
            with open(self.users_file, 'w', encoding='utf-8') as f:
                json.dump(self.users_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Ошибка сохранения пользователей: {e}")
    
    def load_statistics(self) -> Dict:
        """Загрузка статистики"""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {
            "periods": {},
            "total": {
                "registered": 0,
                "questionnaires": 0,
                "broadcasts_sent": 0,
                "messages_received": 0,
                "feedback_received": 0,
                "active_users": 0
            }
        }
    
    def save_statistics(self) -> None:
        """Сохранение статистики"""
        try:
            with open(self.stats_file, 'w', encoding='utf-8') as f:
                json.dump(self.stats_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Ошибка сохранения статистики: {e}")
    
    # Остальные методы остаются такими же, как в предыдущей версии
    # только удалите все вызовы GitHub
    
    def add_user(self, user_id: int, username: str, first_name: str, last_name: str = ""):
        """Добавление нового пользователя"""
        if str(user_id) not in self.users_data["users"]:
            self.users_data["users"][str(user_id)] = {
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "last_activity": datetime.now(pytz.UTC).isoformat(),
                "first_seen": datetime.now(pytz.UTC).isoformat(),
                "questionnaire_answers": {},
                "questionnaires_completed": 0,
                "messages_count": 0,
                "broadcasts_received": 0,
                "feedback_given": False,
                "feedback_count": 0,
                "last_feedback": None,
                "active": True,
                "notifications_enabled": True
            }
            self.save_users()
            self.update_statistics("registered", 1)
            return True
        return False
    
    def update_activity(self, user_id: int, message_type: str = "message"):
        """Обновление активности пользователя"""
        user_id_str = str(user_id)
        
        if user_id_str in self.users_data["users"]:
            user = self.users_data["users"][user_id_str]
            user["last_activity"] = datetime.now(pytz.UTC).isoformat()
            user["active"] = True
            
            if message_type == "message":
                user["messages_count"] = user.get("messages_count", 0) + 1
                self.update_statistics("messages_received", 1)
            
            self.save_users()
    
    def save_questionnaire_answers(self, user_id: int, answers: Dict):
        """Сохранение ответов на анкету"""
        user_id_str = str(user_id)
        
        if user_id_str in self.users_data["users"]:
            user = self.users_data["users"][user_id_str]
            user["questionnaire_answers"] = answers
            user["questionnaires_completed"] = user.get("questionnaires_completed", 0) + 1
            user["questionnaire_completed_at"] = datetime.now(pytz.UTC).isoformat()
            user["last_activity"] = datetime.now(pytz.UTC).isoformat()
            self.save_users()
            self.update_statistics("questionnaires", 1)
    
    def record_feedback(self, user_id: int):
        """Запись обратной связи"""
        user_id_str = str(user_id)
        
        if user_id_str in self.users_data["users"]:
            user = self.users_data["users"][user_id_str]
            user["feedback_given"] = True
            user["feedback_count"] = user.get("feedback_count", 0) + 1
            user["last_feedback"] = datetime.now(pytz.UTC).isoformat()
            user["last_activity"] = datetime.now(pytz.UTC).isoformat()
            self.save_users()
            self.update_statistics("feedback_received", 1)
    
    def record_broadcast(self, user_ids: List[int]):
        """Запись отправки рассылки"""
        for user_id in user_ids:
            user_id_str = str(user_id)
            if user_id_str in self.users_data["users"]:
                user = self.users_data["users"][user_id_str]
                user["broadcasts_received"] = user.get("broadcasts_received", 0) + 1
                user["last_broadcast_received"] = datetime.now(pytz.UTC).isoformat()
        
        self.save_users()
        self.update_statistics("broadcasts_sent", len(user_ids))
    
    def update_statistics(self, metric: str, value: int = 1):
        """Обновление статистики"""
        if metric in self.stats_data["total"]:
            self.stats_data["total"][metric] += value
        
        period_id = self.get_current_period_id()
        if period_id not in self.stats_data["periods"]:
            self.stats_data["periods"][period_id] = {
                "start_date": self.get_period_start_date().isoformat(),
                "end_date": self.get_period_end_date().isoformat(),
                "registered": 0,
                "questionnaires": 0,
                "broadcasts_sent": 0,
                "messages_received": 0,
                "feedback_received": 0,
                "active_users": 0
            }
        
        if metric in self.stats_data["periods"][period_id]:
            self.stats_data["periods"][period_id][metric] += value
        
        self.save_statistics()
    
    def get_current_period_id(self) -> str:
        """ID текущего периода (2 недели)"""
        today = datetime.now(pytz.UTC)
        year_start = datetime(today.year, 1, 1, tzinfo=pytz.UTC)
        days_passed = (today - year_start).days
        period_number = days_passed // 14 + 1
        return f"{today.year}_P{period_number}"
    
    def get_period_start_date(self) -> datetime:
        """Дата начала текущего периода"""
        today = datetime.now(pytz.UTC)
        days_passed = (today - datetime(today.year, 1, 1, tzinfo=pytz.UTC)).days
        period_start_day = (days_passed // 14) * 14
        return datetime(today.year, 1, 1, tzinfo=pytz.UTC) + timedelta(days=period_start_day)
    
    def get_period_end_date(self) -> datetime:
        """Дата окончания текущего периода"""
        start_date = self.get_period_start_date()
        return start_date + timedelta(days=13)
    
    def get_active_users(self, days: int = 14):
        """Получение активных пользователей"""
        active_users = []
        cutoff_date = datetime.now(pytz.UTC) - timedelta(days=days)
        
        for user_id, user_data in self.users_data["users"].items():
            if user_data.get("active", False) and user_data.get("notifications_enabled", True):
                last_activity = datetime.fromisoformat(user_data["last_activity"])
                if last_activity >= cutoff_date:
                    active_users.append((int(user_id), user_data))
        
        return active_users
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Получение данных пользователя"""
        return self.users_data["users"].get(str(user_id))
    
    def get_all_users(self):
        """Получение всех пользователей"""
        return [(int(user_id), user_data) for user_id, user_data in self.users_data["users"].items()]
    
    def get_period_statistics(self, period_id: str = None):
        """Статистика за период"""
        if period_id is None:
            period_id = self.get_current_period_id()
        
        if period_id in self.stats_data["periods"]:
            return self.stats_data["periods"][period_id]
        return None
    
    def get_all_periods(self):
        """Все периоды"""
        return self.stats_data["periods"]
    
    def calculate_activity_metrics(self, days: int = 14):
        """Метрики активности"""
        active_users = self.get_active_users(days)
        total_users = len(self.users_data["users"])
        
        feedback_users = 0
        for user_id, user_data in self.users_data["users"].items():
            if user_data.get("feedback_count", 0) > 0:
                feedback_users += 1
        
        return {
            "total_users": total_users,
            "active_users": len(active_users),
            "activity_rate": (len(active_users) / total_users * 100) if total_users > 0 else 0,
            "feedback_users": feedback_users,
            "feedback_rate": (feedback_users / total_users * 100) if total_users > 0 else 0,
            "avg_messages_per_user": sum([u["messages_count"] for u in self.users_data["users"].values()]) / total_users if total_users > 0 else 0,
            "avg_questionnaires_per_user": sum([u.get("questionnaires_completed", 0) for u in self.users_data["users"].values()]) / total_users if total_users > 0 else 0
        }
