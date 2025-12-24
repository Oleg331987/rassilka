import os
import asyncio
import logging
import json
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# =========== КОНФИГУРАЦИЯ ===========
class Config:
    def __init__(self):
        # Токен бота
        self.BOT_TOKEN = os.getenv("BOT_TOKEN", "8227089023:AAFHtDuflB-wKcxp-bEwfPU0AgD1smFyt5I")
        
        # ID администратора (получить у @userinfobot)
        admin_id = os.getenv("ADMIN_ID", "")
        self.ADMIN_ID = int(admin_id) if admin_id and admin_id.isdigit() else None
        
        # Порт для Railway
        self.PORT = int(os.getenv("PORT", 8080))
        
        # База данных
        self.DB_PATH = "tenders.db"
        
        # Логирование
        self.setup_logging()
        
        print("=" * 50)
        print("🤖 НАСТРОЙКИ БОТА:")
        print(f"   Токен: {'✅' if self.BOT_TOKEN else '❌'}")
        print(f"   Админ ID: {self.ADMIN_ID or 'Не задан'}")
        print(f"   Порт: {self.PORT}")
        print(f"   БД: {self.DB_PATH}")
        print("=" * 50)
    
    def setup_logging(self):
        """Настройка логирования"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

config = Config()
logger = logging.getLogger(__name__)

# =========== ИНИЦИАЛИЗАЦИЯ БОТА ===========
try:
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    logger.info("✅ Бот и диспетчер инициализированы")
except Exception as e:
    logger.error(f"❌ Ошибка инициализации бота: {e}")
    raise

# =========== HEALTHCHECK СЕРВЕР ===========
class HealthCheckHandler(BaseHTTPRequestHandler):
    """Обработчик healthcheck запросов для Railway"""
    def do_GET(self):
        if self.path in ['/', '/health', '/status']:
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            status = {
                "status": "running",
                "service": "Tritika Bot",
                "bot_initialized": True,
                "timestamp": datetime.now().isoformat(),
                "port": config.PORT
            }
            
            self.wfile.write(json.dumps(status).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass

def run_health_server():
    """Запуск HTTP сервера для healthcheck"""
    try:
        server = HTTPServer(('0.0.0.0', config.PORT), HealthCheckHandler)
        logger.info(f"🌐 Healthcheck сервер запущен на порту {config.PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"❌ Ошибка запуска healthcheck сервера: {e}")

# =========== БАЗА ДАННЫХ ===========
class Database:
    def __init__(self, db_path: str = "tenders.db"):
        self.db_path = db_path
    
    async def init_db(self):
        """Инициализация базы данных"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                # Таблица пользователей
                await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                ''')
                
                # Таблица анкет
                await conn.execute('''
                CREATE TABLE IF NOT EXISTS questionnaires (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    full_name TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    email TEXT NOT NULL,
                    activity TEXT NOT NULL,
                    status TEXT DEFAULT 'new',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
                ''')
                
                # Таблица отправленных тендеров
                await conn.execute('''
                CREATE TABLE IF NOT EXISTS tenders_sent (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    questionnaire_id INTEGER,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    file_id TEXT,
                    FOREIGN KEY (questionnaire_id) REFERENCES questionnaires (id)
                )
                ''')
                
                await conn.commit()
                logger.info("✅ База данных инициализирована")
                return True
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации БД: {e}")
            return False
    
    async def add_user(self, user_id: int, username: str, first_name: str, last_name: str = ""):
        """Добавление пользователя в БД"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute('''
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, last_activity)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (user_id, username, first_name, last_name))
                await conn.commit()
                return True
        except Exception as e:
            logger.error(f"❌ Ошибка добавления пользователя: {e}")
            return False
    
    async def save_questionnaire(self, user_id: int, data: dict):
        """Сохранение анкеты"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute('''
                INSERT INTO questionnaires 
                (user_id, full_name, company_name, phone, email, activity)
                VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    user_id,
                    data['full_name'],
                    data['company_name'],
                    data['phone'],
                    data['email'],
                    data['activity']
                ))
                await conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения анкеты: {e}")
            return None
    
    async def get_user_questionnaires(self, user_id: int):
        """Получение анкет пользователя"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                cursor = await conn.execute('''
                SELECT * FROM questionnaires 
                WHERE user_id = ? 
                ORDER BY created_at DESC
                ''', (user_id,))
                return await cursor.fetchall()
        except Exception as e:
            logger.error(f"❌ Ошибка получения анкет: {e}")
            return []
    
    async def get_new_questionnaires(self, limit: int = 10):
        """Получение новых анкет"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                cursor = await conn.execute('''
                SELECT q.*, u.username 
                FROM questionnaires q
                LEFT JOIN users u ON q.user_id = u.user_id
                WHERE q.status = 'new'
                ORDER BY q.created_at DESC
                LIMIT ?
                ''', (limit,))
                return await cursor.fetchall()
        except Exception as e:
            logger.error(f"❌ Ошибка получения новых анкет: {e}")
            return []
    
    async def get_statistics(self):
        """Получение статистики"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                
                # Общее количество анкет
                cursor = await conn.execute("SELECT COUNT(*) as total FROM questionnaires")
                total = await cursor.fetchone()
                
                # Анкеты сегодня
                cursor = await conn.execute("""
                    SELECT COUNT(*) as today FROM questionnaires 
                    WHERE DATE(created_at) = DATE('now')
                """)
                today = await cursor.fetchone()
                
                # Уникальные пользователи
                cursor = await conn.execute("SELECT COUNT(DISTINCT user_id) as users FROM questionnaires")
                users = await cursor.fetchone()
                
                # Новые анкеты
                cursor = await conn.execute("SELECT COUNT(*) as new FROM questionnaires WHERE status = 'new'")
                new = await cursor.fetchone()
                
                return {
                    'total': total['total'],
                    'today': today['today'],
                    'users': users['users'],
                    'new': new['new']
                }
        except Exception as e:
            logger.error(f"❌ Ошибка получения статистики: {e}")
            return None

db = Database(config.DB_PATH)

# =========== КЛАВИАТУРЫ ===========
def get_main_menu(user_id: int = None):
    """Главное меню"""
    is_admin = user_id and config.ADMIN_ID and user_id == config.ADMIN_ID
    
    if is_admin:
        keyboard = [
            [KeyboardButton(text="📊 Новые анкеты"), KeyboardButton(text="📈 Статистика")],
            [KeyboardButton(text="👥 Все пользователи"), KeyboardButton(text="📋 Все анкеты")],
            [KeyboardButton(text="📞 Контакты"), KeyboardButton(text="❓ Помощь")],
            [KeyboardButton(text="👤 Режим пользователя")]
        ]
    else:
        keyboard = [
            [KeyboardButton(text="📝 Заполнить анкету")],
            [KeyboardButton(text="📋 Мои анкеты"), KeyboardButton(text="📞 Контакты")],
            [KeyboardButton(text="❓ Как это работает?"), KeyboardButton(text="ℹ️ О нас")],
            [KeyboardButton(text="🛠️ Техподдержка")]
        ]
    
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )

def get_cancel_keyboard():
    """Клавиатура отмены"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отменить")]],
        resize_keyboard=True
    )

# =========== СОСТОЯНИЯ ===========
class QuestionnaireStates(StatesGroup):
    waiting_full_name = State()
    waiting_company = State()
    waiting_phone = State()
    waiting_email = State()
    waiting_activity = State()

# =========== ОБРАБОТЧИКИ ===========

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Обработчик команды /start"""
    await state.clear()
    
    user = message.from_user
    user_id = user.id
    
    # Сохраняем пользователя в БД
    await db.add_user(
        user_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name or ""
    )
    
    # Определяем, админ ли это
    is_admin = config.ADMIN_ID and user_id == config.ADMIN_ID
    
    if is_admin:
        welcome_text = """
🛠️ <b>Панель администратора Тритика</b>

Добро пожаловать в систему управления ботом!
        
<b>Доступные функции:</b>
• 📊 Просмотр новых анкет
• 📈 Статистика работы бота
• 👥 Управление пользователями
• 📋 Просмотр всех данных

Для перехода в режим пользователя нажмите кнопку ниже.
"""
    else:
        welcome_text = """
🤖 <b>Добро пожаловать в бот "Тритика"!</b>

Мы — сервис по подбору тендеров и госзакупок для бизнеса.

🎯 <b>Что мы делаем:</b>
• Ищем актуальные тендеры по вашей сфере
• Подготавливаем индивидуальные подборки
• Помогаем с документацией для участия
• Консультируем на всех этапах

💰 <b>Первая подборка — бесплатно!</b>

Чтобы начать, заполните анкету — это займет 3-5 минут.
"""
    
    await message.answer(
        welcome_text,
        reply_markup=get_main_menu(user_id)
    )
    logger.info(f"👤 Пользователь {user_id} (@{user.username}) запустил бота")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Помощь"""
    help_text = """
<b>📚 Помощь по боту</b>

<b>Основные команды:</b>
/start - Главное меню
/help - Эта справка
/admin - Панель администратора (только для админов)

<b>Основные функции:</b>
• 📝 Заполнить анкету - для поиска тендеров
• 📋 Мои анкеты - история ваших заявок
• 📞 Контакты - как с нами связаться
• ❓ Как это работает? - описание сервиса

<b>⏰ Время работы поддержки:</b>
Понедельник - Пятница: 9:00 - 18:00 МСК
Суббота: 10:00 - 15:00 МСК
Воскресенье: выходной

<b>📞 Контакты:</b>
Телефон: +7 (XXX) XXX-XX-XX
Email: info@tritika.ru
Telegram: @tritika_support
"""
    await message.answer(help_text)

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    """Вход в админ-панель"""
    user_id = message.from_user.id
    
    if config.ADMIN_ID and user_id == config.ADMIN_ID:
        await state.clear()
        await message.answer(
            "🔐 <b>Вы авторизованы как администратор</b>",
            reply_markup=get_main_menu(user_id)
        )
        logger.info(f"🛠️ Админ {user_id} вошел в панель")
    else:
        await message.answer("⛔ У вас нет прав доступа к панели администратора.")

# =========== ГЛАВНОЕ МЕНЮ ===========

@dp.message(F.text == "📝 Заполнить анкету")
async def start_questionnaire(message: types.Message, state: FSMContext):
    """Начало заполнения анкеты"""
    await state.clear()
    await message.answer(
        "📝 <b>Начинаем заполнение анкеты!</b>\n\n"
        "Мы подберем для вас тендеры по следующим данным.\n\n"
        "<b>Шаг 1 из 5:</b>\n"
        "Введите ваше <b>ФИО полностью</b> (как в паспорте):",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(QuestionnaireStates.waiting_full_name)

@dp.message(F.text == "📋 Мои анкеты")
async def my_questionnaires(message: types.Message):
    """Мои анкеты"""
    user_id = message.from_user.id
    questionnaires = await db.get_user_questionnaires(user_id)
    
    if not questionnaires:
        await message.answer(
            "📭 <b>У вас пока нет заполненных анкет.</b>\n\n"
            "Хотите заполнить анкету для поиска тендеров?",
            reply_markup=get_main_menu(user_id)
        )
        return
    
    response = f"📋 <b>Ваши анкеты ({len(questionnaires)}):</b>\n\n"
    
    for i, q in enumerate(questionnaires, 1):
        date_str = q['created_at'][:10] if q['created_at'] else "??.??.????"
        status_icon = "✅" if q['status'] == 'processed' else "⏳"
        
        response += f"{i}. <b>{q['company_name']}</b>\n"
        response += f"   📅 {date_str} | {status_icon} {q['status']}\n"
        response += f"   📞 {q['phone']}\n"
        response += f"   📧 {q['email']}\n\n"
    
    await message.answer(response)

@dp.message(F.text == "📞 Контакты")
async def show_contacts(message: types.Message):
    """Контакты"""
    contacts_text = """
<b>📞 Контакты компании "Тритика"</b>

<b>Для клиентов:</b>
• Телефон: +7 (XXX) XXX-XX-XX
• Email: clients@tritika.ru
• Telegram: @tritika_clients

<b>Для партнеров:</b>
• Телефон: +7 (XXX) XXX-XX-XX
• Email: partners@tritika.ru

<b>Техническая поддержка:</b>
• Email: support@tritika.ru
• Telegram: @tritika_support

<b>Юридический адрес:</b>
г. Москва, ул. Примерная, д. 1, офис 101

<b>Время работы:</b>
Пн-Пт: 9:00-18:00
Сб: 10:00-15:00
Вс: выходной

<b>Реквизиты:</b>
ООО «ТРИТИКА»
ИНН: 1234567890
ОГРН: 1234567890123
"""
    await message.answer(contacts_text)

@dp.message(F.text == "❓ Как это работает?")
async def how_it_works(message: types.Message):
    """Как это работает"""
    process_text = """
<b>🔄 Как работает наш сервис:</b>

<b>1. Заполнение анкеты</b>
Вы заполняете простую анкету в боте (5 минут)

<b>2. Анализ данных</b>
Наши специалисты анализируют ваш профиль

<b>3. Поиск тендеров</b>
Ищем подходящие тендеры на 50+ площадках

<b>4. Формирование подборки</b>
Подготавливаем персонализированную подборку

<b>5. Отправка результатов</b>
Высылаем вам подборку на email и в Telegram

<b>6. Консультация</b>
Помогаем разобраться в условиях тендеров

<b>⏱️ Сроки:</b>
• Первая подборка: 1-2 часа в рабочее время
• Последующие: до 24 часов

<b>💰 Стоимость:</b>
• Первая подборка: <b>БЕСПЛАТНО</b>
• Дальнейшие: от 5 000 руб./месяц
"""
    await message.answer(process_text)

@dp.message(F.text == "ℹ️ О нас")
async def about_us(message: types.Message):
    """О нас"""
    about_text = """
<b>🏢 О компании "Тритика"</b>

Мы — команда профессионалов в сфере госзакупок и коммерческих тендеров с опытом работы более 7 лет.

<b>Наша миссия:</b>
Сделать участие в тендерах простым и доступным для любого бизнеса.

<b>Что мы делаем:</b>
• Автоматизированный поиск тендеров
• Анализ требований заказчиков
• Подготовка конкурсной документации
• Юридическое сопровождение
• Обучение сотрудников

<b>Наши достижения:</b>
✅ 500+ довольных клиентов
✅ 2 000+ успешных заявок
✅ 5 млрд+ руб. выигранных контрактов
✅ 85% успешных участий

<b>Почему выбирают нас:</b>
1. <b>Экспертиза</b> - 7 лет на рынке
2. <b>Технологии</b> - собственные алгоритмы поиска
3. <b>Поддержка</b> - персональный менеджер
4. <b>Результат</b> - гарантия качества

<b>Начните сотрудничество с бесплатной подборки!</b>
"""
    await message.answer(about_text)

@dp.message(F.text == "🛠️ Техподдержка")
async def tech_support(message: types.Message):
    """Техподдержка"""
    await message.answer(
        "<b>🛠️ Техническая поддержка</b>\n\n"
        "Если у вас возникли проблемы с ботом:\n\n"
        "1. Попробуйте перезапустить бота командой /start\n"
        "2. Очистите кэш приложения Telegram\n"
        "3. Обновите приложение Telegram\n\n"
        "<b>Если проблема осталась:</b>\n"
        "📧 Напишите на support@tritika.ru\n"
        "📱 Или в Telegram: @tritika_tech\n\n"
        "<i>Укажите в сообщении:</i>\n"
        "• Ваш ID: <code>{}</code>\n"
        "• Время возникновения проблемы\n"
        "• Описание проблемы".format(message.from_user.id)
    )

# =========== АНКЕТА ===========

@dp.message(QuestionnaireStates.waiting_full_name)
async def process_full_name(message: types.Message, state: FSMContext):
    """Обработка ФИО"""
    name = message.text.strip()
    if len(name) < 5:
        await message.answer("❌ ФИО должно содержать минимум 5 символов. Попробуйте еще раз:")
        return
    
    await state.update_data(full_name=name)
    await message.answer(
        "✅ <b>ФИО сохранено</b>\n\n"
        "<b>Шаг 2 из 5:</b>\n"
        "Введите <b>название вашей компании</b> (полное официальное название):"
    )
    await state.set_state(QuestionnaireStates.waiting_company)

@dp.message(QuestionnaireStates.waiting_company)
async def process_company(message: types.Message, state: FSMContext):
    """Обработка названия компании"""
    company = message.text.strip()
    if len(company) < 2:
        await message.answer("❌ Название компании слишком короткое. Попробуйте еще раз:")
        return
    
    await state.update_data(company_name=company)
    await message.answer(
        "✅ <b>Название компании сохранено</b>\n\n"
        "<b>Шаг 3 из 5:</b>\n"
        "Введите ваш <b>телефон для связи</b> (в любом формате):"
    )
    await state.set_state(QuestionnaireStates.waiting_phone)

@dp.message(QuestionnaireStates.waiting_phone)
async def process_phone(message: types.Message, state: FSMContext):
    """Обработка телефона"""
    phone = message.text.strip()
    
    # Простая валидация
    digits = ''.join(filter(str.isdigit, phone))
    if len(digits) < 10:
        await message.answer("❌ Неверный формат телефона. Введите еще раз:")
        return
    
    await state.update_data(phone=phone)
    await message.answer(
        "✅ <b>Телефон сохранен</b>\n\n"
        "<b>Шаг 4 из 5:</b>\n"
        "Введите ваш <b>email</b> (на него пришлем подборку тендеров):"
    )
    await state.set_state(QuestionnaireStates.waiting_email)

@dp.message(QuestionnaireStates.waiting_email)
async def process_email(message: types.Message, state: FSMContext):
    """Обработка email"""
    email = message.text.strip().lower()
    
    if '@' not in email or '.' not in email:
        await message.answer("❌ Неверный формат email. Введите еще раз:")
        return
    
    await state.update_data(email=email)
    await message.answer(
        "✅ <b>Email сохранен</b>\n\n"
        "<b>Шаг 5 из 5:</b>\n"
        "Опишите <b>сферу деятельности</b> вашей компании:\n\n"
        "<i>Примеры:</i>\n"
        "• Строительство жилых домов\n"
        "• Поставка продуктов питания\n"
        "• IT-услуги и разработка\n"
        "• Медицинское оборудование\n"
        "• Услуги уборки и клининга"
    )
    await state.set_state(QuestionnaireStates.waiting_activity)

@dp.message(QuestionnaireStates.waiting_activity)
async def process_activity(message: types.Message, state: FSMContext):
    """Завершение анкеты"""
    user_data = await state.get_data()
    user_data['activity'] = message.text.strip()
    user_id = message.from_user.id
    
    # Сохраняем анкету
    questionnaire_id = await db.save_questionnaire(user_id, user_data)
    
    if questionnaire_id:
        # Успешное сохранение
        await message.answer(
            "🎉 <b>Анкета успешно сохранена!</b>\n\n"
            "✅ <b>Ваши данные:</b>\n"
            f"• ФИО: {user_data['full_name']}\n"
            f"• Компания: {user_data['company_name']}\n"
            f"• Телефон: {user_data['phone']}\n"
            f"• Email: {user_data['email']}\n"
            f"• Сфера: {user_data['activity']}\n\n"
            "<b>Что дальше:</b>\n"
            "1. Наши специалисты анализируют вашу сферу\n"
            "2. Ищем подходящие тендеры на всех площадках\n"
            "3. Формируем индивидуальную подборку\n"
            "4. Отправляем вам на email в течение часа\n\n"
            "<i>Следите за сообщениями!</i>",
            reply_markup=get_main_menu(user_id)
        )
        
        # Уведомление админу
        if config.ADMIN_ID:
            try:
                admin_text = f"""
🆕 <b>НОВАЯ АНКЕТА #{questionnaire_id}</b>

👤 <b>Пользователь:</b> @{message.from_user.username or 'без username'}
🆔 <b>ID:</b> {user_id}
🏢 <b>Компания:</b> {user_data['company_name']}
👨‍💼 <b>ФИО:</b> {user_data['full_name']}
📞 <b>Телефон:</b> {user_data['phone']}
📧 <b>Email:</b> {user_data['email']}
🎯 <b>Сфера:</b> {user_data['activity']}

⏰ <b>Время:</b> {datetime.now().strftime('%H:%M %d.%m.%Y')}
"""
                await bot.send_message(config.ADMIN_ID, admin_text)
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление админу: {e}")
        
        logger.info(f"✅ Анкета #{questionnaire_id} сохранена для пользователя {user_id}")
    else:
        # Ошибка сохранения
        await message.answer(
            "❌ <b>Ошибка при сохранении анкеты</b>\n\n"
            "Пожалуйста, попробуйте еще раз позже или свяжитесь с поддержкой.",
            reply_markup=get_main_menu(user_id)
        )
    
    await state.clear()

# =========== АДМИН ПАНЕЛЬ ===========

@dp.message(F.text == "📊 Новые анкеты")
async def admin_new_questionnaires(message: types.Message):
    """Новые анкеты для админа"""
    user_id = message.from_user.id
    
    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    questionnaires = await db.get_new_questionnaires(10)
    
    if not questionnaires:
        await message.answer("📭 Новых анкет нет")
        return
    
    response = f"🆕 <b>Новые анкеты ({len(questionnaires)}):</b>\n\n"
    
    for i, q in enumerate(questionnaires, 1):
        date_str = q['created_at'][:16] if q['created_at'] else "??.?? ??:??"
        response += f"<b>{i}. #{q['id']} - {q['company_name']}</b>\n"
        response += f"👤 {q['full_name']} (@{q['username'] or 'без username'})\n"
        response += f"📞 {q['phone']}\n"
        response += f"📧 {q['email']}\n"
        response += f"🎯 {q['activity'][:50]}...\n"
        response += f"⏰ {date_str}\n\n"
    
    await message.answer(response)

@dp.message(F.text == "📈 Статистика")
async def admin_statistics(message: types.Message):
    """Статистика для админа"""
    user_id = message.from_user.id
    
    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    stats = await db.get_statistics()
    
    if not stats:
        await message.answer("❌ Не удалось получить статистику")
        return
    
    response = f"""
📊 <b>Статистика бота</b>

📋 <b>Анкеты:</b>
• Всего: {stats['total']}
• Сегодня: {stats['today']}
• Новые: {stats['new']}

👥 <b>Пользователи:</b>
• Уникальных: {stats['users']}

📅 <b>Дата отчета:</b>
{datetime.now().strftime('%d.%m.%Y %H:%M')}

🤖 <b>Статус бота:</b>
• Работает ✅
• БД: Активна ✅
• Сервер: Онлайн ✅
"""
    
    await message.answer(response)

@dp.message(F.text == "👥 Все пользователи")
async def admin_all_users(message: types.Message):
    """Все пользователи для админа"""
    user_id = message.from_user.id
    
    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    try:
        async with aiosqlite.connect(config.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute('''
                SELECT u.*, COUNT(q.id) as questionnaire_count
                FROM users u
                LEFT JOIN questionnaires q ON u.user_id = q.user_id
                GROUP BY u.user_id
                ORDER BY u.last_activity DESC
                LIMIT 20
            ''')
            users = await cursor.fetchall()
        
        if not users:
            await message.answer("👥 Пользователей нет")
            return
        
        response = "👥 <b>Последние пользователи (20):</b>\n\n"
        
        for i, user in enumerate(users, 1):
            last_active = user['last_activity'][:16] if user['last_activity'] else "??.?? ??:??"
            response += f"{i}. <b>@{user['username'] or 'без username'}</b>\n"
            response += f"   🆔 ID: {user['user_id']}\n"
            response += f"   👤 {user['first_name']} {user['last_name'] or ''}\n"
            response += f"   📋 Анкет: {user['questionnaire_count']}\n"
            response += f"   ⏰ Активность: {last_active}\n\n"
        
        await message.answer(response)
    except Exception as e:
        logger.error(f"Ошибка получения пользователей: {e}")
        await message.answer("❌ Ошибка получения данных")

@dp.message(F.text == "📋 Все анкеты")
async def admin_all_questionnaires(message: types.Message):
    """Все анкеты для админа"""
    user_id = message.from_user.id
    
    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    try:
        async with aiosqlite.connect(config.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute('''
                SELECT q.*, u.username
                FROM questionnaires q
                LEFT JOIN users u ON q.user_id = u.user_id
                ORDER BY q.created_at DESC
                LIMIT 10
            ''')
            questionnaires = await cursor.fetchall()
        
        if not questionnaires:
            await message.answer("📭 Анкет нет")
            return
        
        response = f"📋 <b>Все анкеты (последние 10):</b>\n\n"
        
        for i, q in enumerate(questionnaires, 1):
            date_str = q['created_at'][:16] if q['created_at'] else "??.?? ??:??"
            status_icon = "✅" if q['status'] == 'processed' else "⏳"
            
            response += f"<b>{i}. #{q['id']} - {q['company_name']}</b>\n"
            response += f"   👤 {q['full_name']} (@{q['username'] or 'без username'})\n"
            response += f"   📞 {q['phone']}\n"
            response += f"   📧 {q['email']}\n"
            response += f"   🎯 {q['activity'][:30]}...\n"
            response += f"   📅 {date_str} | {status_icon} {q['status']}\n\n"
        
        await message.answer(response)
    except Exception as e:
        logger.error(f"Ошибка получения всех анкет: {e}")
        await message.answer("❌ Ошибка получения данных")

@dp.message(F.text == "👤 Режим пользователя")
async def admin_user_mode(message: types.Message, state: FSMContext):
    """Переключение в режим пользователя"""
    user_id = message.from_user.id
    
    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    await state.clear()
    await message.answer(
        "👤 <b>Вы перешли в режим пользователя</b>\n\n"
        "Теперь вы можете тестировать функции бота как обычный пользователь.\n\n"
        "Чтобы вернуться в панель администратора, используйте команду /admin",
        reply_markup=get_main_menu(123)  # Любой ID, не админ
    )

# =========== ОБРАБОТЧИК ОТМЕНЫ ===========

@dp.message(F.text == "❌ Отменить")
async def cancel_handler(message: types.Message, state: FSMContext):
    """Отмена текущего действия"""
    current_state = await state.get_state()
    
    if current_state is None:
        await message.answer("❌ Нечего отменять")
        return
    
    await state.clear()
    user_id = message.from_user.id
    is_admin = config.ADMIN_ID and user_id == config.ADMIN_ID
    
    await message.answer(
        "❌ Действие отменено",
        reply_markup=get_main_menu(user_id) if not is_admin else get_main_menu(user_id)
    )

# =========== ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ ===========

@dp.message()
async def handle_unknown(message: types.Message):
    """Обработчик неизвестных сообщений"""
    user_id = message.from_user.id
    is_admin = config.ADMIN_ID and user_id == config.ADMIN_ID
    
    response = """
🤖 <b>Я вас не понял</b>

Пожалуйста, используйте кнопки меню или команды:

<b>Основные команды:</b>
/start - Главное меню
/help - Справка по боту

<b>Или выберите действие из меню:</b>
"""
    
    await message.answer(response, reply_markup=get_main_menu(user_id))

# =========== ЗАПУСК БОТА ===========

async def main():
    """Основная функция запуска бота"""
    print("\n" + "="*60)
    print("🚀 ЗАПУСК БОТА ТРИТИКА")
    print("="*60)
    
    # 1. Проверяем токен бота
    print("\n🔍 Проверка токена бота...")
    try:
        bot_info = await bot.get_me()
        print(f"✅ Бот авторизован: @{bot_info.username}")
        print(f"   Имя: {bot_info.first_name}")
        print(f"   ID: {bot_info.id}")
    except Exception as e:
        print(f"❌ Ошибка авторизации бота: {e}")
        print("   Проверьте BOT_TOKEN в переменных окружения")
        print("   Текущий токен:", config.BOT_TOKEN[:20] + "..." if len(config.BOT_TOKEN) > 20 else config.BOT_TOKEN)
        return
    
    # 2. Инициализируем базу данных
    print("\n🗄️ Инициализация базы данных...")
    if await db.init_db():
        print("✅ База данных готова")
    else:
        print("❌ Ошибка инициализации БД")
        print("⚠️ Продолжаем без БД...")
    
    # 3. Запускаем healthcheck сервер в отдельном потоке
    print(f"\n🌐 Запуск healthcheck сервера на порту {config.PORT}...")
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    print("✅ Healthcheck сервер запущен")
    
    # 4. Удаляем старый вебхук
    print("\n🔄 Очистка старых вебхуков...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("✅ Вебхуки очищены")
    except Exception as e:
        print(f"⚠️ Не удалось очистить вебхуки: {e}")
    
    # 5. Запускаем бота в режиме polling
    print("\n" + "="*60)
    print("🤖 БОТ УСПЕШНО ЗАПУЩЕН!")
    print("="*60)
    print(f"\n📱 Откройте Telegram и найдите бота:")
    print(f"   👉 https://t.me/{bot_info.username}")
    print("\n📊 Админ-панель доступна по команде: /admin")
    print("👤 Обычный режим: /start")
    print("\n🔄 Бот ожидает сообщений...\n")
    
    try:
        await dp.start_polling(bot, skip_updates=True)
    except KeyboardInterrupt:
        print("\n\n🛑 Бот остановлен пользователем")
    except Exception as e:
        print(f"\n💥 Критическая ошибка: {e}")
    finally:
        await bot.session.close()
        print("👋 Сессия бота закрыта")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Программа завершена")
    except Exception as e:
        print(f"\n💥 Фатальная ошибка: {e}")
        import traceback
        traceback.print_exc()
