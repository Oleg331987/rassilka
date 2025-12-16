import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import BOT_TOKEN, ADMIN_IDS, COMPANY_INFO
from database import SimpleDatabase
from questionnaire import Questionnaire, QuestionnaireStates
from report_generator import ReportGenerator

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db = SimpleDatabase()  # Локальная база данных без GitHub
questionnaire = Questionnaire()

# =========== КОМАНДЫ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ===========
@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Начало работы с ботом"""
    user = message.from_user
    db.add_user(user.id, user.username, user.first_name, user.last_name)
    db.update_activity(user.id)
    
    await message.answer(
        f"👋 Добро пожаловать, {user.first_name}!\n\n"
        "Я бот компании ООО \"Тритика\"\n"
        "Помогаю найти подходящие тендеры для вашего бизнеса\n\n"
        "📋 Основные команды:\n"
        "/questionnaire - Заполнить анкету для поиска тендеров\n"
        "/my_data - Посмотреть мою анкету\n"
        "/settings - Настройки уведомлений\n"
        "/feedback - Оставить отзыв\n"
        "/help - Помощь\n\n"
        "ℹ️ Каждые 2 недели вы будете получать полезную информацию о тендерах"
    )

@dp.message(Command("questionnaire"))
async def cmd_questionnaire(message: Message, state: FSMContext):
    """Заполнение анкеты"""
    db.update_activity(message.from_user.id)
    await questionnaire.start_questionnaire(message, state)

@dp.message(QuestionnaireStates.answering)
async def handle_questionnaire_answer(message: Message, state: FSMContext):
    """Обработка ответов анкеты"""
    db.update_activity(message.from_user.id)
    await questionnaire.handle_answer(message, state)

@dp.message(Command("my_data"))
async def cmd_my_data(message: Message):
    """Просмотр своих данных"""
    user_id = message.from_user.id
    db.update_activity(user_id)
    
    user_data = db.get_user(user_id)
    
    if not user_data:
        await message.answer("Сначала используйте /start")
        return
    
    # Основная информация
    last_activity = user_data["last_activity"][:19].replace("T", " ")
    created_at = user_data["first_seen"][:19].replace("T", " ")
    
    text = (
        f"📊 Ваши данные в боте:\n\n"
        f"👤 Имя: {user_data['first_name']} {user_data.get('last_name', '')}\n"
        f"📱 Username: @{user_data.get('username', 'нет')}\n"
        f"🆔 ID: {user_id}\n"
        f"📅 Регистрация: {created_at}\n"
        f"⏰ Последняя активность: {last_activity}\n"
        f"🔔 Уведомления: {'Включены' if user_data.get('notifications_enabled', True) else 'Выключены'}\n"
        f"📊 Ваша статистика:\n"
        f"  • Заполнено анкет: {user_data.get('questionnaires_completed', 0)}\n"
        f"  • Отправлено сообщений: {user_data.get('messages_count', 0)}\n"
        f"  • Получено рассылок: {user_data.get('broadcasts_received', 0)}\n"
        f"  • Оставлено отзывов: {user_data.get('feedback_count', 0)}\n"
    )
    
    # Данные анкеты
    answers = user_data.get("questionnaire_answers", {})
    if answers:
        text += "\n📝 Данные последней анкеты:\n"
        fields_display = {
            'company_name': 'Компания',
            'inn': 'ИНН',
            'contact_person': 'Контактное лицо',
            'phone': 'Телефон',
            'email': 'Email',
            'okved': 'ОКВЭД',
            'industry_keywords': 'Отрасль и ключевые слова',
            'contract_amount': 'Сумма контракта',
            'regions': 'Регионы'
        }
        
        for field, display in fields_display.items():
            if field in answers:
                value = answers[field]
                if len(str(value)) > 100:
                    text += f"• {display}: {str(value)[:100]}...\n"
                else:
                    text += f"• {display}: {value}\n"
        
        if "questionnaire_completed_at" in user_data:
            completed_at = user_data["questionnaire_completed_at"][:19].replace("T", " ")
            text += f"\n📅 Дата заполнения: {completed_at}"
        
        # Кнопка для просмотра полной анкеты
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📋 Показать полную анкету", callback_data="show_full_questionnaire")],
                [InlineKeyboardButton(text="✏️ Заполнить новую анкету", callback_data="new_questionnaire")]
            ]
        )
        await message.answer(text, reply_markup=keyboard)
    else:
        text += "\n📝 Анкета: НЕ заполнена\n"
        text += "Используйте /questionnaire чтобы заполнить анкету"
        await message.answer(text)

@dp.message(Command("feedback"))
async def cmd_feedback(message: Message):
    """Оставление отзыва"""
    db.update_activity(message.from_user.id)
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Отлично", callback_data="feedback_excellent")],
            [InlineKeyboardButton(text="👍 Хорошо", callback_data="feedback_good")],
            [InlineKeyboardButton(text="😐 Удовлетворительно", callback_data="feedback_ok")],
            [InlineKeyboardButton(text="👎 Плохо", callback_data="feedback_bad")],
            [InlineKeyboardButton(text="📝 Текстовый отзыв", callback_data="feedback_text")]
        ]
    )
    
    await message.answer(
        "💬 Пожалуйста, оцените нашу работу:\n\n"
        "Как вам наш сервис по поиску тендеров?",
        reply_markup=keyboard
    )

@dp.message(Command("settings"))
async def cmd_settings(message: Message):
    """Настройки бота"""
    db.update_activity(message.from_user.id)
    
    user_data = db.get_user(message.from_user.id)
    notifications_enabled = user_data.get("notifications_enabled", True) if user_data else True
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'🔕' if notifications_enabled else '🔔'} {'Выключить' if notifications_enabled else 'Включить'} уведомления", 
                    callback_data="toggle_notifications"
                )
            ],
            [
                InlineKeyboardButton(text="📝 Заполнить анкету заново", callback_data="restart_questionnaire"),
                InlineKeyboardButton(text="💬 Оставить отзыв", callback_data="give_feedback")
            ]
        ]
    )
    
    await message.answer(
        "⚙️ Настройки бота:\n\n"
        f"• Уведомления: {'🔔 Включены' if notifications_enabled else '🔕 Выключены'}\n"
        "• Рассылка информации: каждые 2 недели\n"
        "• Данные хранятся локально\n"
        "⚠️ Внимание: на Render.com данные могут сбрасываться при перезапуске",
        reply_markup=keyboard
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Помощь по боту"""
    db.update_activity(message.from_user.id)
    
    help_text = (
        "🆘 Помощь по боту ООО \"Тритика\":\n\n"
        "1. 📝 Заполнение анкеты:\n"
        "   Используйте /questionnaire\n"
        "   Ответьте на 9 вопросов о вашей компании\n"
        "   Получите подборку подходящих тендеров\n\n"
        "2. ⚙️ Настройки:\n"
        "   /settings - управление уведомлениями\n"
        "   /my_data - просмотр вашей анкеты\n"
        "   /feedback - оставить отзыв\n\n"
        "3. 🔔 Уведомления:\n"
        "   Каждые 2 недели - полезная информация\n"
        "   Только для активных пользователей\n\n"
        "4. 📞 Контакты компании:\n"
        "   ООО \"Тритика\"\n"
        "   Телефон: +7 (4922) 223-222\n"
        "   Адрес: г. Владимир, ул. Разина, д. 51, оф. 37\n\n"
        "5. 🛡️ Безопасность:\n"
        "   Все данные хранятся безопасно\n"
        "   Используются только для подбора тендеров\n\n"
        "⚠️ Важно: на бесплатном хостинге данные могут временно храниться\n"
        "Для постоянного хранения рассмотрите платный тариф\n\n"
        "Для начала работы используйте /questionnaire"
    )
    
    await message.answer(help_text)

# =========== КОЛЛБЭКИ ===========
@dp.callback_query(F.data == "show_full_questionnaire")
async def show_full_questionnaire(callback: types.CallbackQuery):
    """Показать полную анкету"""
    user_id = callback.from_user.id
    user_data = db.get_user(user_id)
    
    if not user_data or not user_data.get("questionnaire_answers"):
        await callback.answer("Анкета не найдена")
        return
    
    answers = user_data["questionnaire_answers"]
    report = questionnaire.generate_report(answers)
    
    # Разбиваем на части если слишком длинное
    if len(report) > 4000:
        parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
        for part in parts:
            await callback.message.answer(part)
    else:
        await callback.message.answer(report)
    
    await callback.answer()

@dp.callback_query(F.data == "new_questionnaire")
async def new_questionnaire(callback: types.CallbackQuery, state: FSMContext):
    """Начать новую анкету"""
    await cmd_questionnaire(callback.message, state)
    await callback.answer()

@dp.callback_query(F.data == "toggle_notifications")
async def toggle_notifications(callback: types.CallbackQuery):
    """Переключить уведомления"""
    user_id = callback.from_user.id
    user_data = db.get_user(user_id)
    
    if user_data:
        current = user_data.get("notifications_enabled", True)
        user_data["notifications_enabled"] = not current
        db.save_users()
        
        status = "включены" if not current else "выключены"
        await callback.message.edit_text(
            f"✅ Уведомления {status}\n\n"
            "Теперь вы будете получать рассылку каждые 2 недели" if not current else
            "🔕 Уведомления отключены\n\n"
            "Вы не будете получать рассылку"
        )
    else:
        await callback.answer("Ошибка изменения настроек")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("feedback_"))
async def handle_feedback(callback: types.CallbackQuery):
    """Обработка отзывов"""
    feedback_type = callback.data.replace("feedback_", "")
    user_id = callback.from_user.id
    
    # Записываем отзыв
    db.record_feedback(user_id)
    
    # Определяем текст ответа
    responses = {
        "excellent": "🎉 Спасибо за отличную оценку! Мы рады, что вам нравится наш сервис!",
        "good": "👍 Спасибо за хорошую оценку! Мы будем стараться еще лучше!",
        "ok": "🙂 Спасибо за оценку! Мы учтем ваши пожелания!",
        "bad": "😔 Нам жаль, что сервис вас не устроил. Мы работаем над улучшениями!"
    }
    
    response = responses.get(feedback_type, "Спасибо за отзыв!")
    
    if feedback_type == "text":
        await callback.message.answer(
            "📝 Пожалуйста, напишите ваш отзыв текстом:\n\n"
            "Что вам понравилось, а что можно улучшить?"
        )
    else:
        await callback.message.answer(response)
    
    await callback.answer()

@dp.callback_query(F.data == "give_feedback")
async def give_feedback_callback(callback: types.CallbackQuery):
    """Оставить отзыв через кнопку"""
    await cmd_feedback(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "restart_questionnaire")
async def restart_questionnaire_callback(callback: types.CallbackQuery, state: FSMContext):
    """Перезапустить анкету"""
    await cmd_questionnaire(callback.message, state)
    await callback.answer()

# =========== АДМИН КОМАНДЫ ===========
@dp.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message):
    """Статистика для администратора"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав для этой команды")
        return
    
    db.update_activity(message.from_user.id)
    
    # Общая статистика
    total_stats = db.stats_data["total"]
    activity_metrics = db.calculate_activity_metrics(14)
    
    stats = (
        f"📊 ОБЩАЯ СТАТИСТИКА БОТА:\n\n"
        f"👥 Пользователи:\n"
        f"• Всего: {total_stats.get('registered', 0)}\n"
        f"• Активных (14 дней): {activity_metrics['active_users']}\n"
        f"• Активность: {activity_metrics['activity_rate']:.1f}%\n\n"
        
        f"📝 Анкеты:\n"
        f"• Всего заполнено: {total_stats.get('questionnaires', 0)}\n"
        f"• Конверсия: {(total_stats.get('questionnaires', 0) / total_stats.get('registered', 0) * 100) if total_stats.get('registered', 0) > 0 else 0:.1f}%\n\n"
        
        f"📨 Рассылки:\n"
        f"• Отправлено: {total_stats.get('broadcasts_sent', 0)}\n"
        f"• Получено сообщений: {total_stats.get('messages_received', 0)}\n\n"
        
        f"💬 Обратная связь:\n"
        f"• Получено отзывов: {total_stats.get('feedback_received', 0)}\n"
        f"• Конверсия в отзывы: {(total_stats.get('feedback_received', 0) / total_stats.get('questionnaires', 0) * 100) if total_stats.get('questionnaires', 0) > 0 else 0:.1f}%\n"
    )
    
    await message.answer(stats)

@dp.message(Command("admin_report"))
async def cmd_admin_report(message: Message):
    """Подробный отчет для администратора"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав для этой команды")
        return
    
    db.update_activity(message.from_user.id)
    
    await message.answer("📊 Формирую подробный отчет...")
    
    # Генерируем отчет
    report_gen = ReportGenerator(db)
    period_id = db.get_current_period_id()
    period_stats = db.get_period_statistics(period_id)
    
    if period_stats:
        report = report_gen.generate_efficiency_report(period_id, period_stats)
        
        # Разбиваем на части если слишком длинный
        if len(report) > 4000:
            parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
            for part in parts:
                await message.answer(part)
        else:
            await message.answer(report)
    else:
        await message.answer("Нет данных для отчета за текущий период")

@dp.message(Command("admin_users"))
async def cmd_admin_users(message: Message):
    """Список пользователей для администратора"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав для этой команды")
        return
    
    db.update_activity(message.from_user.id)
    
    all_users = db.get_all_users()
    
    response = f"👥 Всего пользователей: {len(all_users)}\n\n"
    
    # Показываем последних 10 пользователей
    recent_users = sorted(all_users, key=lambda x: x[1].get("first_seen", ""), reverse=True)[:10]
    
    for i, (user_id, user_data) in enumerate(recent_users, 1):
        first_seen = user_data["first_seen"][:10]
        last_activity = user_data["last_activity"][:10]
        questionnaires = user_data.get("questionnaires_completed", 0)
        
        response += (
            f"{i}. {user_data['first_name']} (@{user_data.get('username', 'нет')})\n"
            f"   ID: {user_id}\n"
            f"   Рег.: {first_seen}, Актив.: {last_activity}\n"
            f"   Анкет: {questionnaires}, Сообщ.: {user_data.get('messages_count', 0)}\n\n"
        )
    
    if len(all_users) > 10:
        response += f"... и еще {len(all_users) - 10} пользователей\n"
    
    await message.answer(response)

@dp.message(Command("admin_broadcast"))
async def cmd_admin_broadcast(message: Message):
    """Ручная рассылка для администратора"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав для этой команды")
        return
    
    await message.answer("📢 Запускаю ручную рассылку...")
    
    # Вызываем функцию рассылки
    success_count, failed_count = await send_broadcast_to_active_users()
    
    await message.answer(
        f"✅ Рассылка завершена!\n"
        f"• Успешно отправлено: {success_count}\n"
        f"• Ошибок: {failed_count}"
    )

@dp.message(Command("admin_export"))
async def cmd_admin_export(message: Message):
    """Экспорт данных для администратора"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав для этой команды")
        return
    
    db.update_activity(message.from_user.id)
    
    try:
        # Формируем JSON данные
        export_data = {
            "users": db.users_data["users"],
            "statistics": db.stats_data,
            "export_date": datetime.now().isoformat(),
            "total_users": len(db.users_data["users"]),
            "total_questionnaires": db.stats_data["total"].get("questionnaires", 0)
        }
        
        import json
        export_json = json.dumps(export_data, ensure_ascii=False, indent=2)
        
        # Отправляем как текстовое сообщение (урезанное)
        if len(export_json) > 4000:
            await message.answer("📁 Данные слишком большие для отправки в Telegram\nИспользуйте локальный экспорт на сервере")
        else:
            await message.answer(f"📁 Экспорт данных:\n```json\n{export_json[:3800]}\n```", parse_mode="Markdown")
            
        await message.answer(
            "💾 Данные хранятся в локальных файлах:\n"
            "• users.json - данные пользователей\n"
            "• statistics.json - статистика\n\n"
            "⚠️ На Render.com файлы временные и могут быть удалены"
        )
        
    except Exception as e:
        await message.answer(f"❌ Ошибка экспорта: {str(e)}")

# =========== ФУНКЦИИ РАССЫЛКИ ===========
async def send_broadcast_to_active_users():
    """Рассылка информации активным пользователям"""
    active_users = db.get_active_users(14)
    user_ids = [user_id for user_id, _ in active_users]
    
    if not user_ids:
        logger.info("Нет активных пользователей для рассылки")
        return 0, 0
    
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
            logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
            failed_count += 1
    
    logger.info(f"Рассылка отправлена: {success_count} успешно, {failed_count} ошибок")
    
    # Записываем статистику рассылки
    db.record_broadcast(user_ids)
    
    # Обновляем время последней рассылки
    db.users_data["last_broadcast"] = datetime.now().isoformat()
    db.save_users()
    
    return success_count, failed_count

async def send_efficiency_report_to_admins():
    """Отправка отчета эффективности администраторам"""
    report_gen = ReportGenerator(db)
    period_id = db.get_current_period_id()
    period_stats = db.get_period_statistics(period_id)
    
    if not period_stats:
        logger.info("Нет данных для отчета за текущий период")
        return
    
    # Генерируем отчет
    report = report_gen.generate_efficiency_report(period_id, period_stats)
    
    # Отправляем администраторам
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=report[:4000]  # Ограничение Telegram
            )
            logger.info(f"Отчет эффективности отправлен администратору {admin_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки отчета администратору {admin_id}: {e}")

# =========== ОБРАБОТКА ВСЕХ СООБЩЕНИЙ ===========
@dp.message()
async def handle_all_messages(message: Message):
    """Обработка всех сообщений"""
    user_id = message.from_user.id
    
    # Обновляем активность
    db.update_activity(user_id)
    
    # Проверяем, есть ли пользователь в базе
    user_data = db.get_user(user_id)
    if not user_data:
        # Если пользователь не зарегистрирован, регистрируем
        user = message.from_user
        db.add_user(user.id, user.username, user.first_name, user.last_name)
    
    # Если пользователь просто написал что-то без команды
    if not message.text.startswith('/'):
        # Проверяем, не является ли это отзывом
        if "отзыв" in message.text.lower() or "feedback" in message.text.lower():
            db.record_feedback(user_id)
            await message.answer(
                "💬 Спасибо за ваш отзыв! Мы учтем ваши пожелания.\n\n"
                "Для получения помощи используйте /help"
            )
        else:
            await message.answer(
                "Для поиска тендеров используйте команду /questionnaire\n"
                "Для помощи - /help\n"
                "Для просмотра ваших данных - /my_data\n"
                "Для отзыва - /feedback"
            )

# =========== ЗАПУСК БОТА ===========
async def start_bot():
    """Запуск бота"""
    logger.info("Бот запущен")
    
    # Удаляем вебхук и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)
