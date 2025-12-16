import re
import json
from datetime import datetime
from typing import Dict, Any
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram import types
from config import QUESTIONNAIRE

class QuestionnaireStates(StatesGroup):
    answering = State()

class Questionnaire:
    def __init__(self):
        self.questions = QUESTIONNAIRE
        self.current_question_index = {}
    
    async def start_questionnaire(self, message: types.Message, state: FSMContext):
        """Начало анкеты"""
        user_id = message.from_user.id
        self.current_question_index[user_id] = 0
        
        await state.set_state(QuestionnaireStates.answering)
        await state.update_data(answers={})
        
        await message.answer(
            "📝 Заполнение анкеты для поиска тендеров\n\n"
            "Пожалуйста, ответьте на вопросы. Всего вопросов: 9\n\n"
            "Вопрос 1/9:\n"
            f"{self.questions[0]['question']}"
        )
    
    async def handle_answer(self, message: types.Message, state: FSMContext):
        """Обработка ответа на вопрос"""
        user_id = message.from_user.id
        
        # Получаем текущие данные
        data = await state.get_data()
        answers = data.get("answers", {})
        
        # Получаем текущий вопрос
        current_index = self.current_question_index.get(user_id, 0)
        question_data = self.questions[current_index]
        
        # Проверяем ответ
        is_valid, validated_data = await self.validate_answer(message.text, question_data)
        
        if not is_valid:
            await message.answer(f"❌ {validated_data}\nПожалуйста, исправьте ответ.")
            return
        
        # Сохраняем ответ
        answers[question_data["field"]] = message.text.strip()
        
        # Обновляем индекс вопроса
        current_index += 1
        self.current_question_index[user_id] = current_index
        
        if current_index < len(self.questions):
            # Задаем следующий вопрос
            await state.update_data(answers=answers)
            await message.answer(
                f"Вопрос {current_index + 1}/{len(self.questions)}:\n"
                f"{self.questions[current_index]['question']}"
            )
        else:
            # Анкета завершена
            await self.complete_questionnaire(message, answers, state)
    
    async def validate_answer(self, answer: str, question_data: dict) -> tuple[bool, Any]:
        """Валидация ответа"""
        answer = answer.strip()
        
        if not answer:
            return False, "Ответ не может быть пустым"
        
        field_type = question_data["type"]
        
        if field_type == "text":
            return True, answer
        
        elif field_type == "number":
            # Проверка ИНН
            if question_data["field"] == "inn":
                if not answer.isdigit():
                    return False, "ИНН должен содержать только цифры"
                if len(answer) not in [10, 12]:
                    return False, "ИНН должен содержать 10 цифр (для организаций) или 12 цифр (для ИП)"
            return True, answer
        
        elif field_type == "phone":
            # Проверка телефона (упрощенная)
            phone_clean = re.sub(r'[\s\-\(\)]', '', answer)
            if phone_clean.startswith('+7') and len(phone_clean) == 12:
                return True, answer
            elif phone_clean.startswith('8') and len(phone_clean) == 11:
                return True, answer
            elif phone_clean.startswith('7') and len(phone_clean) == 11:
                return True, '+7' + phone_clean[1:]
            else:
                return False, "Неверный формат телефона. Используйте +7 XXX XXX-XX-XX"
        
        elif field_type == "email":
            # Проверка email
            pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if re.match(pattern, answer):
                return True, answer
            return False, "Неверный формат email. Пример: ivanov@company.ru"
        
        return True, answer
    
    async def complete_questionnaire(self, message: types.Message, answers: dict, state: FSMContext):
        """Завершение анкеты"""
        user_id = message.from_user.id
        
        # Формируем отчет
        report = self.generate_report(answers)
        
        # Сохраняем в базу данных
        from bot_core import db
        db.save_questionnaire_answers(user_id, answers)
        
        # Сбрасываем состояние
        await state.clear()
        
        # Отправляем отчет
        await message.answer(
            "✅ Анкета успешно заполнена!\n"
            "📋 Ваши данные сохранены.\n\n"
            "Сформированный отчет:"
        )
        
        await message.answer(report)
        
        # Имитация выгрузки тендеров
        await message.answer(
            "🔍 Ищу тендеры по вашим критериям..."
        )
        
        tender_results = self.generate_tender_results(answers)
        await message.answer(tender_results)
        
        # Кнопка для обратной связи
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Оставить отзыв", callback_data="give_feedback")],
                [InlineKeyboardButton(text="📊 Запросить статистику", callback_data="request_stats")]
            ]
        )
        
        await message.answer(
            "Понравилась ли вам выгрузка? Оставьте отзыв, чтобы мы могли улучшить сервис!",
            reply_markup=keyboard
        )
        
        # Очищаем индекс вопроса
        if user_id in self.current_question_index:
            del self.current_question_index[user_id]
    
    def generate_report(self, answers: dict) -> str:
        """Генерация отчета по анкете"""
        report = "📋 АНКЕТА ДЛЯ ПОИСКА ТОРГОВ\n\n"
        
        report += f"1. Наименование компании:\n{answers.get('company_name', 'Не указано')}\n\n"
        report += f"2. ИНН:\n{answers.get('inn', 'Не указано')}\n\n"
        report += f"3. Контактное лицо (ФИО/должность):\n{answers.get('contact_person', 'Не указано')}\n\n"
        report += f"4. Телефон:\n{answers.get('phone', 'Не указано')}\n\n"
        report += f"5. E-mail:\n{answers.get('email', 'Не указано')}\n\n"
        report += f"6. Сфера деятельности, ОКВЭД (основные):\n{answers.get('okved', 'Не указано')}\n\n"
        report += f"7. Отрасль / Ключевые слова /ОКПД2:\n{answers.get('industry_keywords', 'Не указано')}\n\n"
        report += f"8. Сумма контракта:\n{answers.get('contract_amount', 'Не указано')}\n\n"
        report += f"9. Регионы исполнения контрактов:\n{answers.get('regions', 'Не указано')}\n\n"
        
        report += "=" * 50 + "\n"
        report += f"Дата заполнения: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        report += "ООО \"Тритика\"\n"
        report += "Телефон: +7 (4922) 223-222"
        
        return report
    
    def generate_tender_results(self, answers: dict) -> str:
        """Генерация результатов поиска тендеров (имитация)"""
        results = "📊 Результаты поиска тендеров:\n\n"
        results += "По вашим критериям найдено подходящих тендеров: 8\n\n"
        
        results += "🎯 Рекомендуемые тендеры:\n"
        results += "1. Поставка офисной техники\n"
        results += "   • Заказчик: Администрация г. Владимир\n"
        results += "   • Сумма: 1 200 000 руб.\n"
        results += "   • Срок подачи: 7 дней\n\n"
        
        results += "2. Ремонт помещений\n"
        results += "   • Заказчик: МБОУ СОШ №1\n"
        results += "   • Сумма: 850 000 руб.\n"
        results += "   • Срок подачи: 10 дней\n\n"
        
        results += "3. Разработка сайта\n"
        results += "   • Заказчик: ООО \"БизнесТех\"\n"
        results += "   • Сумма: 300 000 руб.\n"
        results += "   • Срок подади: 14 дней\n\n"
        
        results += "💼 Для участия в тендерах:\n"
        results += "• Получите электронную подпись (ЭЦП)\n"
        results += "• Зарегистрируйтесь на электронных площадках\n"
        results += "• Подготовьте пакет документов\n\n"
        
        results += "📞 Мы можем помочь с подготовкой к участию!\n"
        results += "Телефон: +7 (4922) 223-222"
        
        return results
