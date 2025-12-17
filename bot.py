import os
import sqlite3
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Получаем токен и ID админа
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен!")
    exit(1)

# Инициализация бота
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('tenders.db')
    cursor = conn.cursor()
    
    # Таблица для анкет
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS questionnaires (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        company_name TEXT,
        inn TEXT,
        contact_person TEXT,
        phone TEXT,
        email TEXT,
        activity_sphere TEXT,
        industry TEXT,
        contract_amount TEXT,
        regions TEXT,
        status TEXT DEFAULT 'new',
        created_at TEXT,
        updated_at TEXT,
        admin_comment TEXT
    )
    ''')
    
    # Таблица для отправленных тендеров
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tenders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        questionnaire_id INTEGER,
        title TEXT,
        description TEXT,
        link TEXT,
        price TEXT,
        deadline TEXT,
        admin_id INTEGER,
        sent_at TEXT,
        FOREIGN KEY (questionnaire_id) REFERENCES questionnaires (id)
    )
    ''')
    
    conn.commit()
    conn.close()

# Инициализируем БД при старте
init_db()

# Определяем состояния для анкеты
class Questionnaire(StatesGroup):
    company_name = State()
    inn = State()
    contact_person = State()
    phone = State()
    email = State()
    activity_sphere = State()
    industry = State()
    contract_amount = State()
    regions = State()

# =========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===========
def get_main_keyboard():
    """Главное меню"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету")],
            [KeyboardButton(text="📋 Мои данные"), KeyboardButton(text="📊 Мои заявки")],
            [KeyboardButton(text="ℹ️ Помощь"), KeyboardButton(text="📞 Контакты")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие"
    )

def get_admin_keyboard():
    """Клавиатура администратора"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Все заявки"), KeyboardButton(text="🆕 Новые заявки")],
            [KeyboardButton(text="📤 Отправить тендер"), KeyboardButton(text="📈 Статистика")],
            [KeyboardButton(text="🏠 Главное меню")]
        ],
        resize_keyboard=True
    )

def get_cancel_keyboard():
    """Клавиатура для отмены"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚫 Отменить")]
        ],
        resize_keyboard=True
    )

def save_questionnaire(user_data):
    """Сохраняем анкету в базу данных"""
    try:
        conn = sqlite3.connect('tenders.db')
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO questionnaires 
        (user_id, username, company_name, inn, contact_person, phone, email, 
         activity_sphere, industry, contract_amount, regions, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_data['user_id'],
            user_data['username'],
            user_data['company_name'],
            user_data['inn'],
            user_data['contact_person'],
            user_data['phone'],
            user_data['email'],
            user_data['activity_sphere'],
            user_data['industry'],
            user_data['contract_amount'],
            user_data['regions'],
            user_data['created_at'],
            user_data['created_at']
        ))
        
        conn.commit()
        questionnaire_id = cursor.lastrowid
        conn.close()
        
        return questionnaire_id
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения анкеты: {e}")
        return None

def get_user_questionnaires(user_id):
    """Получаем анкеты пользователя"""
    try:
        conn = sqlite3.connect('tenders.db')
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT id, company_name, status, created_at, admin_comment 
        FROM questionnaires 
        WHERE user_id = ? 
        ORDER BY created_at DESC
        ''', (user_id,))
        
        questionnaires = cursor.fetchall()
        conn.close()
        
        return questionnaires
    except Exception as e:
        logger.error(f"❌ Ошибка получения анкет: {e}")
        return []

def get_questionnaire_by_id(questionnaire_id):
    """Получаем анкету по ID"""
    try:
        conn = sqlite3.connect('tenders.db')
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT * FROM questionnaires WHERE id = ?
        ''', (questionnaire_id,))
        
        questionnaire = cursor.fetchone()
        conn.close()
        
        return questionnaire
    except Exception as e:
        logger.error(f"❌ Ошибка получения анкеты: {e}")
        return None

def get_all_questionnaires(status=None):
    """Получаем все анкеты (или по статусу)"""
    try:
        conn = sqlite3.connect('tenders.db')
        cursor = conn.cursor()
        
        if status:
            cursor.execute('''
            SELECT * FROM questionnaires WHERE status = ? ORDER BY created_at DESC
            ''', (status,))
        else:
            cursor.execute(''SELECT * FROM questionnaires ORDER BY created_at DESC''')
        
        questionnaires = cursor.fetchall()
        conn.close()
        
        return questionnaires
    except Exception as e:
        logger.error(f"❌ Ошибка получения анкет: {e}")
        return []

def update_questionnaire_status(questionnaire_id, status, admin_comment=None):
    """Обновляем статус анкеты"""
    try:
        conn = sqlite3.connect('tenders.db')
        cursor = conn.cursor()
        
        if admin_comment:
            cursor.execute('''
            UPDATE questionnaires 
            SET status = ?, admin_comment = ?, updated_at = ?
            WHERE id = ?
            ''', (status, admin_comment, datetime.now().isoformat(), questionnaire_id))
        else:
            cursor.execute('''
            UPDATE questionnaires 
            SET status = ?, updated_at = ?
            WHERE id = ?
            ''', (status, datetime.now().isoformat(), questionnaire_id))
        
        conn.commit()
        conn.close()
        
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка обновления статуса: {e}")
        return False

def save_tender(questionnaire_id, title, description, link, price, deadline, admin_id):
    """Сохраняем отправленный тендер"""
    try:
        conn = sqlite3.connect('tenders.db')
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO tenders 
        (questionnaire_id, title, description, link, price, deadline, admin_id, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            questionnaire_id, title, description, link, price, deadline, 
            admin_id, datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()
        
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения тендера: {e}")
        return False

def get_tenders_for_user(user_id):
    """Получаем тендеры для пользователя"""
    try:
        conn = sqlite3.connect('tenders.db')
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT t.title, t.description, t.link, t.price, t.deadline, t.sent_at
        FROM tenders t
        JOIN questionnaires q ON t.questionnaire_id = q.id
        WHERE q.user_id = ?
        ORDER BY t.sent_at DESC
        ''', (user_id,))
        
        tenders = cursor.fetchall()
        conn.close()
        
        return tenders
    except Exception as e:
        logger.error(f"❌ Ошибка получения тендеров: {e}")
        return []

# =========== КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ===========
@dp.message(Command("start"))
@dp.message(F.text == "🏠 Главное меню")
async def cmd_start(message: types.Message):
    """Начало работы с ботом"""
    # Проверяем, является ли пользователь администратором
    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            "👑 <b>Добро пожаловать, администратор!</b>\n\n"
            "Вы можете управлять заявками и отправлять тендеры пользователям.",
            reply_markup=get_admin_keyboard()
        )
    else:
        await message.answer(
            "🏢 <b>Добро пожаловать в бот для поиска тендеров!</b>\n\n"
            "Я помогу вам найти подходящие тендеры для вашего бизнеса.\n\n"
            "<b>Как это работает:</b>\n"
            "1. 📝 Заполняете анкету\n"
            "2. 📊 Мы анализируем ваши потребности\n"
            "3. 🎯 Подбираем подходящие тендеры\n"
            "4. 📨 Отправляем вам варианты в Telegram\n\n"
            "Выберите действие ниже 👇",
            reply_markup=get_main_keyboard()
        )

@dp.message(F.text == "📝 Заполнить анкету")
async def start_questionnaire(message: types.Message, state: FSMContext):
    """Начало заполнения анкеты"""
    current_state = await state.get_state()
    if current_state:
        await message.answer("Вы уже заполняете анкету. Закончите или отмените её.")
        return
    
    await message.answer(
        "📋 <b>Начинаем заполнение анкеты для поиска тендеров</b>\n\n"
        "Пожалуйста, отвечайте на вопросы внимательно.\n"
        "Это поможет нам точнее подобрать тендеры.\n\n"
        "<b>Введите полное название вашей компании:</b>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.company_name)

@dp.message(Questionnaire.company_name)
async def process_company_name(message: types.Message, state: FSMContext):
    if len(message.text) < 2:
        await message.answer("❌ Название слишком короткое. Введите полное название:")
        return
    
    await state.update_data(company_name=message.text)
    await message.answer(
        "✅ <b>Сохранено!</b>\n\n"
        "<b>Введите ИНН вашей компании (10 или 12 цифр):</b>\n"
        "<i>Пример: 1234567890</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.inn)

@dp.message(Questionnaire.inn)
async def process_inn(message: types.Message, state: FSMContext):
    inn = message.text.strip()
    if not (inn.isdigit() and len(inn) in [10, 12]):
        await message.answer("❌ ИНН должен содержать 10 или 12 цифр. Введите снова:")
        return
    
    await state.update_data(inn=inn)
    await message.answer(
        "✅ <b>Сохранено!</b>\n\n"
        "<b>Введите ФИО контактного лица:</b>\n"
        "<i>Пример: Иванов Иван Иванович</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.contact_person)

@dp.message(Questionnaire.contact_person)
async def process_contact_person(message: types.Message, state: FSMContext):
    await state.update_data(contact_person=message.text)
    await message.answer(
        "✅ <b>Сохранено!</b>\n\n"
        "<b>Введите контактный телефон:</b>\n"
        "<i>Пример: +7 999 123-45-67</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.phone)

@dp.message(Questionnaire.phone)
async def process_phone(message: types.Message, state: FSMContext):
    await state.update_data(phone=message.text)
    await message.answer(
        "✅ <b>Сохранено!</b>\n\n"
        "<b>Введите email для связи:</b>\n"
        "<i>Пример: info@company.ru</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.email)

@dp.message(Questionnaire.email)
async def process_email(message: types.Message, state: FSMContext):
    email = message.text.strip()
    if "@" not in email:
        await message.answer("❌ Введите корректный email адрес:")
        return
    
    await state.update_data(email=email)
    await message.answer(
        "✅ <b>Сохранено!</b>\n\n"
        "<b>Введите сферу деятельности (ОКВЭД):</b>\n"
        "<i>Пример: Строительство зданий, ОКВЭД 41.20</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.activity_sphere)

@dp.message(Questionnaire.activity_sphere)
async def process_activity_sphere(message: types.Message, state: FSMContext):
    await state.update_data(activity_sphere=message.text)
    await message.answer(
        "✅ <b>Сохранено!</b>\n\n"
        "<b>Введите ключевые слова для поиска:</b>\n"
        "<i>Пример: строительство, ремонт, отделочные работы</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.industry)

@dp.message(Questionnaire.industry)
async def process_industry(message: types.Message, state: FSMContext):
    await state.update_data(industry=message.text)
    await message.answer(
        "✅ <b>Сохранено!</b>\n\n"
        "<b>Введите желаемую сумму контракта:</b>\n"
        "<i>Пример: от 100 000 до 500 000 рублей</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.contract_amount)

@dp.message(Questionnaire.contract_amount)
async def process_contract_amount(message: types.Message, state: FSMContext):
    await state.update_data(contract_amount=message.text)
    await message.answer(
        "✅ <b>Сохранено!</b>\n\n"
        "<b>Введите регионы для работы:</b>\n"
        "<i>Пример: Москва, Московская область, Владимир</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.regions)

@dp.message(Questionnaire.regions)
async def process_regions(message: types.Message, state: FSMContext):
    """Завершение анкеты"""
    # Получаем все данные
    user_data = await state.get_data()
    
    # Добавляем дополнительную информацию
    user_data['user_id'] = message.from_user.id
    user_data['username'] = message.from_user.username or "Не указан"
    user_data['regions'] = message.text
    user_data['created_at'] = datetime.now().isoformat()
    
    # Сохраняем в базу данных
    questionnaire_id = save_questionnaire(user_data)
    
    if questionnaire_id:
        # Отправляем уведомление всем администраторам
        admin_message = f"""
        📋 <b>НОВАЯ АНКЕТА #{questionnaire_id}</b>
        
        👤 <b>Пользователь:</b> @{user_data['username']}
        🆔 <b>ID:</b> {user_data['user_id']}
        📅 <b>Дата:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}
        
        🏢 <b>Компания:</b> {user_data['company_name']}
        🔢 <b>ИНН:</b> {user_data['inn']}
        👤 <b>Контактное лицо:</b> {user_data['contact_person']}
        📞 <b>Телефон:</b> {user_data['phone']}
        📧 <b>Email:</b> {user_data['email']}
        
        Для просмотра полной анкеты используйте /view_{questionnaire_id}
        """
        
        # Отправляем всем администраторам
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, admin_message)
            except Exception as e:
                logger.error(f"❌ Ошибка отправки администратору {admin_id}: {e}")
        
        # Сообщение пользователю
        await message.answer(
            f"🎉 <b>Анкета успешно заполнена!</b>\n\n"
            f"✅ Ваша заявка №{questionnaire_id} принята.\n"
            f"📊 Наши специалисты уже ищут для вас тендеры.\n\n"
            f"<b>Статус заявки:</b> 🔄 В обработке\n"
            f"<b>Ожидайте ответа в этом чате.</b>\n\n"
            f"Спасибо за доверие!",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            "❌ <b>Произошла ошибка при сохранении анкеты.</b>\n\n"
            "Пожалуйста, попробуйте позже или свяжитесь с нами.",
            reply_markup=get_main_keyboard()
        )
    
    await state.clear()

@dp.message(F.text == "📋 Мои данные")
async def show_my_data(message: types.Message):
    """Показываем последнюю анкету пользователя"""
    questionnaires = get_user_questionnaires(message.from_user.id)
    
    if not questionnaires:
        await message.answer(
            "📭 <b>У вас пока нет заполненных анкет.</b>\n\n"
            "Нажмите '📝 Заполнить анкету', чтобы начать поиск тендеров.",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Берем последнюю анкету
    last_q = questionnaires[0]
    status_emoji = {
        'new': '🆕',
        'processing': '🔄',
        'completed': '✅',
        'rejected': '❌'
    }.get(last_q[2], '❓')
    
    response = f"""
    📋 <b>Ваша последняя анкета #{last_q[0]}</b>
    
    <b>Статус:</b> {status_emoji} {last_q[2].capitalize()}
    <b>Дата создания:</b> {last_q[3][:10]}
    
    <b>Компания:</b> {last_q[1]}
    
    {f'<b>Комментарий администратора:</b> {last_q[4]}' if last_q[4] else ''}
    
    Для просмотра всех анкет нажмите "📊 Мои заявки"
    """
    
    await message.answer(response, reply_markup=get_main_keyboard())

@dp.message(F.text == "📊 Мои заявки")
async def show_my_questionnaires(message: types.Message):
    """Показываем все заявки пользователя"""
    questionnaires = get_user_questionnaires(message.from_user.id)
    
    if not questionnaires:
        await message.answer(
            "📭 <b>У вас пока нет заявок.</b>\n\n"
            "Нажмите '📝 Заполнить анкету', чтобы начать поиск тендеров.",
            reply_markup=get_main_keyboard()
        )
        return
    
    response = "📊 <b>Ваши заявки:</b>\n\n"
    
    for q in questionnaires:
        status_emoji = {
            'new': '🆕',
            'processing': '🔄',
            'completed': '✅',
            'rejected': '❌'
        }.get(q[2], '❓')
        
        response += f"""
        <b>Заявка #{q[0]}</b>
        🏢 {q[1][:30]}...
        📅 {q[3][:10]}
        📊 {status_emoji} {q[2].capitalize()}
        ──────────────────────
        """
    
    response += "\nДля просмотра деталей конкретной заявки напишите /view_номер (например, /view_1)"
    
    await message.answer(response, reply_markup=get_main_keyboard())

@dp.message(F.text == "📞 Контакты")
async def show_contacts(message: types.Message):
    """Показываем контакты компании"""
    await message.answer(
        "📞 <b>Контакты компании</b>\n\n"
        "<b>ООО \"Тритика\"</b>\n"
        "📍 Адрес: г. Владимир\n"
        "📱 Телефон: +7 (4922) 223-222\n"
        "✉️ Email: info@tritika.ru\n"
        "🌐 Сайт: www.tritika.ru\n\n"
        "<b>График работы:</b>\n"
        "Пн-Пт: 9:00-18:00\n"
        "Сб: 10:00-15:00\n"
        "Вс: выходной",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "ℹ️ Помощь")
async def show_help(message: types.Message):
    """Показываем помощь"""
    await message.answer(
        "ℹ️ <b>Помощь по использованию бота</b>\n\n"
        "<b>Основные функции:</b>\n"
        "• 📝 Заполнить анкету - создать новую заявку\n"
        "• 📋 Мои данные - посмотреть последнюю анкету\n"
        "• 📊 Мои заявки - список всех заявок\n"
        "• 📞 Контакты - контакты компании\n\n"
        "<b>Процесс работы:</b>\n"
        "1. Заполняете анкету\n"
        "2. Мы обрабатываем заявку\n"
        "3. Ищем подходящие тендеры\n"
        "4. Отправляем вам результаты\n\n"
        "<b>Статусы заявок:</b>\n"
        "🆕 Новая - заявка принята\n"
        "🔄 В обработке - ищем тендеры\n"
        "✅ Завершена - тендеры отправлены\n"
        "❌ Отклонена - смотрите комментарий\n\n"
        "Есть вопросы? Напишите нам!",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "🚫 Отменить")
async def cancel_questionnaire(message: types.Message, state: FSMContext):
    """Отмена заполнения анкеты"""
    await message.answer(
        "Заполнение анкеты отменено.",
        reply_markup=get_main_keyboard()
    )
    await state.clear()

# =========== КОМАНДЫ АДМИНИСТРАТОРА ===========
@dp.message(F.text == "📊 Все заявки")
async def admin_all_questionnaires(message: types.Message):
    """Показать все заявки (админ)"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    questionnaires = get_all_questionnaires()
    
    if not questionnaires:
        await message.answer("📭 Заявок пока нет.", reply_markup=get_admin_keyboard())
        return
    
    response = "📊 <b>Все заявки:</b>\n\n"
    
    for q in questionnaires[:10]:  # Ограничиваем 10 заявками
        status_emoji = {
            'new': '🆕',
            'processing': '🔄',
            'completed': '✅',
            'rejected': '❌'
        }.get(q[12], '❓')
        
        response += f"""
        <b>#{q[0]}</b> - {q[3][:20]}...
        👤 @{q[2] or 'нет'} | 📅 {q[13][:10]}
        {status_emoji} {q[12].upper()}
        ──────────────────────
        """
    
    if len(questionnaires) > 10:
        response += f"\n... и еще {len(questionnaires) - 10} заявок"
    
    response += "\n\nДля просмотра деталей: /view_номер"
    response += "\nДля изменения статуса: /status_номер_статус"
    
    await message.answer(response, reply_markup=get_admin_keyboard())

@dp.message(F.text == "🆕 Новые заявки")
async def admin_new_questionnaires(message: types.Message):
    """Показать новые заявки (админ)"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    questionnaires = get_all_questionnaires('new')
    
    if not questionnaires:
        await message.answer("🆕 Новых заявок нет.", reply_markup=get_admin_keyboard())
        return
    
    response = "🆕 <b>Новые заявки:</b>\n\n"
    
    for q in questionnaires:
        response += f"""
        <b>#{q[0]}</b> - {q[3]}
        👤 @{q[2] or 'нет'} | 📞 {q[6]}
        📅 {q[13][:16]}
        ──────────────────────
        """
    
    response += "\nДля просмотра деталей: /view_номер"
    response += "\nДля отправки тендера: /tender_номер"
    
    await message.answer(response, reply_markup=get_admin_keyboard())

@dp.message(F.text == "📤 Отправить тендер")
async def admin_send_tender_menu(message: types.Message, state: FSMContext):
    """Меню отправки тендера"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    await message.answer(
        "📤 <b>Отправка тендера пользователю</b>\n\n"
        "Введите номер заявки в формате:\n"
        "<code>/tender_1</code>\n\n"
        "Или напишите номер заявки:",
        reply_markup=get_admin_keyboard()
    )

# Обработка команды /view_{id}
@dp.message(F.text.regexp(r'^/view_(\d+)$'))
async def view_questionnaire(message: types.Message):
    """Просмотр конкретной анкеты"""
    try:
        questionnaire_id = int(message.text.split('_')[1])
        questionnaire = get_questionnaire_by_id(questionnaire_id)
        
        if not questionnaire:
            await message.answer("❌ Заявка не найдена.")
            return
        
        # Проверяем права доступа
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS and user_id != questionnaire[1]:
            await message.answer("❌ У вас нет доступа к этой заявке.")
            return
        
        status_emoji = {
            'new': '🆕',
            'processing': '🔄',
            'completed': '✅',
            'rejected': '❌'
        }.get(questionnaire[12], '❓')
        
        response = f"""
        📋 <b>Заявка #{questionnaire[0]}</b>
        
        <b>Статус:</b> {status_emoji} {questionnaire[12].upper()}
        <b>Создана:</b> {questionnaire[13][:16]}
        <b>Обновлена:</b> {questionnaire[14][:16] if questionnaire[14] else '—'}
        
        <b>👤 Пользователь:</b> @{questionnaire[2] or 'нет'}
        <b>🆔 User ID:</b> {questionnaire[1]}
        
        <b>🏢 Компания:</b> {questionnaire[3]}
        <b>🔢 ИНН:</b> {questionnaire[4]}
        <b>👤 Контактное лицо:</b> {questionnaire[5]}
        <b>📞 Телефон:</b> {questionnaire[6]}
        <b>📧 Email:</b> {questionnaire[7]}
        
        <b>🏭 Сфера деятельности:</b> {questionnaire[8]}
        <b>🔑 Ключевые слова:</b> {questionnaire[9]}
        <b>💰 Сумма контракта:</b> {questionnaire[10]}
        <b>🌍 Регионы:</b> {questionnaire[11]}
        
        {f'<b>💬 Комментарий:</b> {questionnaire[15]}' if questionnaire[15] else ''}
        """
        
        keyboard = None
        if user_id in ADMIN_IDS:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ В обработку", callback_data=f"status_{questionnaire_id}_processing"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"status_{questionnaire_id}_rejected")
                ],
                [
                    InlineKeyboardButton(text="🎯 Отправить тендер", callback_data=f"send_tender_{questionnaire_id}"),
                    InlineKeyboardButton(text="✅ Завершить", callback_data=f"status_{questionnaire_id}_completed")
                ]
            ])
        
        await message.answer(response, reply_markup=keyboard)
        
    except (ValueError, IndexError):
        await message.answer("❌ Неверный формат команды. Используйте: /view_номер")

# Обработка callback-запросов для изменения статуса
@dp.callback_query(F.data.startswith("status_"))
async def process_status_change(callback: types.CallbackQuery):
    """Обработка изменения статуса заявки"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ У вас нет прав для этого действия.")
        return
    
    try:
        _, questionnaire_id, new_status = callback.data.split("_")
        questionnaire_id = int(questionnaire_id)
        
        # Обновляем статус
        if update_questionnaire_status(questionnaire_id, new_status):
            status_names = {
                'new': 'Новая',
                'processing': 'В обработке',
                'completed': 'Завершена',
                'rejected': 'Отклонена'
            }
            
            await callback.message.edit_text(
                f"{callback.message.text}\n\n"
                f"✅ <b>Статус изменен на: {status_names.get(new_status, new_status)}</b>"
            )
            
            # Уведомляем пользователя об изменении статуса
            questionnaire = get_questionnaire_by_id(questionnaire_id)
            if questionnaire:
                user_id = questionnaire[1]
                status_message = f"""
                📢 <b>Обновление по вашей заявке #{questionnaire_id}</b>
                
                Статус изменен: <b>{status_names.get(new_status, new_status)}</b>
                
                Компания: {questionnaire[3]}
                """
                
                try:
                    await bot.send_message(user_id, status_message)
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки уведомления пользователю: {e}")
            
            await callback.answer("✅ Статус обновлен!")
        else:
            await callback.answer("❌ Ошибка обновления статуса.")
            
    except Exception as e:
        logger.error(f"❌ Ошибка изменения статуса: {e}")
        await callback.answer("❌ Произошла ошибка.")

# Обработка отправки тендера
@dp.callback_query(F.data.startswith("send_tender_"))
async def start_send_tender(callback: types.CallbackQuery, state: FSMContext):
    """Начинаем процесс отправки тендера"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ У вас нет прав для этого действия.")
        return
    
    try:
        questionnaire_id = int(callback.data.split("_")[2])
        
        # Сохраняем ID анкеты в состояние
        await state.update_data(tender_questionnaire_id=questionnaire_id)
        
        await callback.message.answer(
            f"📤 <b>Отправка тендера для заявки #{questionnaire_id}</b>\n\n"
            "Введите <b>название тендера</b>:",
            reply_markup=get_cancel_keyboard()
        )
        
        class TenderStates(StatesGroup):
            title = State()
            description = State()
            link = State()
            price = State()
            deadline = State()
        
        await state.set_state(TenderStates.title)
        await callback.answer()
        
    except Exception as e:
        logger.error(f"❌ Ошибка начала отправки тендера: {e}")
        await callback.answer("❌ Произошла ошибка.")

# =========== ОБРАБОТКА ТЕНДЕРОВ ===========
class TenderStates(StatesGroup):
    title = State()
    description = State()
    link = State()
    price = State()
    deadline = State()

@dp.message(TenderStates.title)
async def process_tender_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer(
        "✅ <b>Название сохранено</b>\n\n"
        "Введите <b>описание тендера</b>:"
    )
    await state.set_state(TenderStates.description)

@dp.message(TenderStates.description)
async def process_tender_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer(
        "✅ <b>Описание сохранено</b>\n\n"
        "Введите <b>ссылку на тендер</b>:"
    )
    await state.set_state(TenderStates.link)

@dp.message(TenderStates.link)
async def process_tender_link(message: types.Message, state: FSMContext):
    await state.update_data(link=message.text)
    await message.answer(
        "✅ <b>Ссылка сохранена</b>\n\n"
        "Введите <b>стоимость/бюджет</b>:"
    )
    await state.set_state(TenderStates.price)

@dp.message(TenderStates.price)
async def process_tender_price(message: types.Message, state: FSMContext):
    await state.update_data(price=message.text)
    await message.answer(
        "✅ <b>Стоимость сохранена</b>\n\n"
        "Введите <b>срок подачи заявок</b>:"
    )
    await state.set_state(TenderStates.deadline)

@dp.message(TenderStates.deadline)
async def process_tender_deadline(message: types.Message, state: FSMContext):
    """Завершение создания тендера"""
    tender_data = await state.get_data()
    tender_data['deadline'] = message.text
    
    questionnaire_id = tender_data.get('tender_questionnaire_id')
    
    if not questionnaire_id:
        await message.answer("❌ Ошибка: не найден ID анкеты.")
        await state.clear()
        return
    
    # Сохраняем тендер в базу
    if save_tender(
        questionnaire_id=questionnaire_id,
        title=tender_data['title'],
        description=tender_data['description'],
        link=tender_data['link'],
        price=tender_data['price'],
        deadline=tender_data['deadline'],
        admin_id=message.from_user.id
    ):
        # Получаем данные анкеты
        questionnaire = get_questionnaire_by_id(questionnaire_id)
        
        if questionnaire:
            # Отправляем тендер пользователю
            user_id = questionnaire[1]
            
            tender_message = f"""
            🎯 <b>ДЛЯ ВАС НАЙДЕН ТЕНДЕР!</b>
            
            <b>Заявка:</b> #{questionnaire_id}
            <b>Компания:</b> {questionnaire[3]}
            
            ──────────────────────
            
            <b>📋 Название:</b> {tender_data['title']}
            
            <b>📝 Описание:</b>
            {tender_data['description']}
            
            <b>💰 Бюджет:</b> {tender_data['price']}
            <b>⏰ Срок подачи:</b> {tender_data['deadline']}
            
            <b>🔗 Ссылка:</b> {tender_data['link']}
            
            ──────────────────────
            
            <b>📞 Контакты для связи:</b>
            +7 (4922) 223-222
            info@tritika.ru
            
            Удачи в участии! 🏆
            """
            
            try:
                await bot.send_message(user_id, tender_message)
                
                # Обновляем статус анкеты
                update_questionnaire_status(questionnaire_id, 'completed', 
                                          'Тендер отправлен пользователю')
                
                # Уведомляем администратора
                await message.answer(
                    f"✅ <b>Тендер успешно отправлен!</b>\n\n"
                    f"Заявка: #{questionnaire_id}\n"
                    f"Пользователь: @{questionnaire[2] or 'нет'}\n"
                    f"Тендер: {tender_data['title'][:50]}...",
                    reply_markup=get_admin_keyboard()
                )
                
            except Exception as e:
                logger.error(f"❌ Ошибка отправки тендера пользователю: {e}")
                await message.answer(
                    "❌ Не удалось отправить тендер пользователю. "
                    "Возможно, он заблокировал бота.",
                    reply_markup=get_admin_keyboard()
                )
        else:
            await message.answer(
                "❌ Анкета не найдена.",
                reply_markup=get_admin_keyboard()
            )
    else:
        await message.answer(
            "❌ Ошибка сохранения тендера.",
            reply_markup=get_admin_keyboard()
        )
    
    await state.clear()

# =========== ЗАПУСК БОТА ===========
async def main():
    """Запуск бота"""
    logger.info("🚀 Запуск бота для поиска тендеров...")
    logger.info(f"✅ База данных инициализирована")
    logger.info(f"✅ Администраторы: {ADMIN_IDS}")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
