import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import BOT_TOKEN, GITHUB_TOKEN, GITHUB_REPO, ADMIN_IDS, COMPANY_INFO
from database import GitHubDatabase
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
db = GitHubDatabase(github_token=GITHUB_TOKEN, repo_name=GITHUB_REPO)
questionnaire = Questionnaire()

# Команды для пользователей
@dp.message(Command("start"))
async def cmd_start(message: Message):
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
    db.update_activity(message.from_user.id)
    await questionnaire.start_questionnaire(message, state)

@dp.message(QuestionnaireStates.answering)
async def handle_questionnaire_answer(message: Message, state: FSMContext):
    db.update_activity(message.from_user.id)
    await questionnaire.handle_answer(message, state)

@dp.message(Command("my_data"))
async def cmd_my_data(message: Message):
    user_id = message.from_user.id
    db.update_activity(user_id)
    
    user_data = db.get_user(user_id)
    
    if not user_data:
        await message.answer("Сначала используйте /start")
        return
    
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
        "• Данные хранятся в защищенном хранилище",
        reply_markup=keyboard
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    db.update_activity(message.from_user.id)
    
    help_text = (
        "🆘 Помощь по боту ООО \"Тритика\":\n\n"
        "1. 📝 Заполнение анкеты:\n"
        "   Используйте /questionnaire\n"
        "   Ответьте на 9 вопросов о вашей компании\n"
        "   Получите подборку подходящих тендеров\n\n"
        "2. ⚙️ Настройки:\n"
        "   /settings - управление уведомлений\n"
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
        "Для начала работы используйте /questionnaire"
    )
    
    await message.answer(help_text)

# Коллбэки
@dp.callback_query(F.data == "show_full_questionnaire")
async def show_full_questionnaire(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_data = db.get_user(user_id)
    
    if not user_data or not user_data.get("questionnaire_answers"):
        await callback.answer("Анкета не найдена")
        return
    
    answers = user_data["questionnaire_answers"]
    report = questionnaire.generate_report(answers)
    
    if len(report) > 4000:
        parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
        for part in parts:
            await callback.message.answer(part)
    else:
        await callback.message.answer(report)
    
    await callback.answer()

@dp.callback_query(F.data == "new_questionnaire")
async def new_questionnaire(callback: types.CallbackQuery, state: FSMContext):
    await cmd_questionnaire(callback.message, state)
    await callback.answer()

@dp.callback_query(F.data == "toggle_notifications")
async def toggle_notifications(callback: types.CallbackQuery):
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
    feedback_type = callback.data.replace("feedback_", "")
    user_id = callback.from_user.id
    
    db.record_feedback(user_id)
    
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
    await cmd_feedback(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "restart_questionnaire")
async def restart_questionnaire_callback(callback: types.CallbackQuery, state: FSMContext):
    await cmd_questionnaire(callback.message, state)
    await callback.answer()

# Админ команды
@dp.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав для этой команды")
        return
    
    db.update_activity(message.from_user.id)
    
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
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав для этой команды")
        return
    
    db.update_activity(message.from_user.id)
    
    await message.answer("📊 Формирую подробный отчет...")
    
    report_gen = ReportGenerator(db)
    period_id = db.get_current_period_id()
    period_stats = db.get_period_statistics(period_id)
    
    if period_stats:
        report = report_gen.generate_efficiency_report(period_id, period_stats)
        
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
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав для этой команды")
        return
    
    db.update_activity(message.from_user.id)
    
    all_users = db.get_all_users()
    
    response = f"👥 Всего пользователей: {len(all_users)}\n\n"
    
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

# Обработка всех сообщений
@dp.message()
async def handle_all_messages(message: Message):
    user_id = message.from_user.id
    
    db.update_activity(user_id)
    
    user_data = db.get_user(user_id)
    if not user_data:
        user = message.from_user
        db.add_user(user.id, user.username, user.first_name, user.last_name)
    
    if not message.text.startswith('/'):
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

# Функции для рассылки
async def send_broadcast_to_active_users():
    """Рассылка информации активным пользователям"""
    active_users = db.get_active_users(14)
    user_ids = [user_id for user_id, _ in active_users]
    
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
            print(f"Ошибка отправки пользователю {user_id}: {e}")
            failed_count += 1
    
    print(f"Рассылка отправлена: {success_count} успешно, {failed_count} ошибок")
    db.record_broadcast(user_ids)
    
    db.users_data["last_broadcast"] = datetime.now(pytz.UTC).isoformat()
    db.save_users()
    
    return success_count, failed_count

async def send_efficiency_report_to_admins():
    """Отправка отчета эффективности администраторам"""
    report_gen = ReportGenerator(db)
    period_id = db.get_current_period_id()
    period_stats = db.get_period_statistics(period_id)
    
    if not period_stats:
        print("Нет данных для отчета за текущий период")
        return
    
    report = report_gen.generate_efficiency_report(period_id, period_stats)
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=report[:4000]
            )
            print(f"Отчет эффективности отправлен администратору {admin_id}")
        except Exception as e:
            print(f"Ошибка отправки отчета администратору {admin_id}: {e}")

# Запуск бота
async def start_bot():
    logger.info("Бот запущен")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)
