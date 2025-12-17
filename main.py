import os
import sqlite3
import logging
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from background import keep_alive

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
    logger.error("❌ BOT_TOKEN не установлен! Добавьте его в Secrets.")
    exit(1)

# Инициализация бота
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
scheduler = AsyncIOScheduler()

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('tenders.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS questionnaires (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        full_name TEXT,
        company_name TEXT,
        inn TEXT,
        contact_person TEXT,
        phone TEXT,
        email TEXT,
        activity_sphere TEXT,
        industry TEXT,
        contract_amount TEXT,
        regions TEXT,
        status TEXT DEFAULT 'processing',
        created_at TEXT,
        updated_at TEXT,
        admin_comment TEXT
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS newsletter_subscribers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        username TEXT,
        subscribed_at TEXT
    )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# =========== СОСТОЯНИЯ ===========
class Questionnaire(StatesGroup):
    waiting_for_name = State()
    waiting_for_company = State()
    waiting_for_inn = State()
    waiting_for_contact = State()
    waiting_for_phone = State()
    waiting_for_email = State()
    waiting_for_activity = State()
    waiting_for_industry = State()
    waiting_for_amount = State()
    waiting_for_regions = State()

class AdminTender(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_tender_title = State()
    waiting_for_tender_description = State()
    waiting_for_tender_link = State()
    waiting_for_tender_amount = State()
    waiting_for_tender_deadline = State()

# =========== ТЕКСТЫ ДЛЯ БОТА ===========
RESPONSE_TEMPLATES = {
    "request_received": """
✅ <b>Запрос получен!</b>

Благодарим вас за обращение в наш сервис. Мы уже начали поиск тендеров по вашим параметрам.

Обработка запроса и формирование персональной подборки займет не более 1-го часа.

Как только выгрузка будет готова, мы пришлем ее в этот чат.

<b>Следите за обновлениями!</b>
—
Всегда на связи, команда ТРИТИКА.
Телефон: +7 (904) 653-69-87
Сайт: https://tritika.ru/
E-mail: info@tritika.ru
""",
    
    "tender_export": """
📄 <b>ВЫГРУЗКА ТЕНДЕРОВ | ТРИТИКА</b>

*Сформировано для вас на основе запроса.
————————————————
👤 <b>ДАННЫЕ КЛИЕНТА:</b>
• Запрос от: {full_name}
• Сфера: {activity_sphere}
• Регион поиска: {regions}
• Ключевые слова: {industry}
• Время запроса: {created_at}
————————————————
📊 <b>РЕЗУЛЬТАТЫ ПОИСКА:</b>
Найдено потенциально подходящих торгов: {tender_count}
————————————————
💡 <b>ВАЖНО:</b>
• Данная подборка сформирована автоматически и носит информационный характер.
• Внимательно изучайте документацию перед участием.
• Актуальные условия могут меняться, проверяйте информацию на площадках заказчиков.
————————————————
❓ <b>ВОПРОСЫ?</b>
Мы всегда на связи для консультации.

С уважением, команда ТРИТИКА.
https://tritika.ru/
""",
    
    "file_sent": """
✅ <b>Ваша персональная подборка готова!</b>

Во вложении вы найдете файл с детальной выгрузкой тендеров, соответствующих вашим критериям.

📎 <b>Файл: {filename}</b>
👉 Если возникнут вопросы по конкретным тендерам — обращайтесь!
""",
    
    "no_tenders_found": """
🔍 <b>РЕЗУЛЬТАТЫ ПОИСКА</b>

К сожалению, по вашим текущим параметрам ({activity_sphere}, {regions}, {industry}) в системе Seldon на данный момент не найдено активных торгов.

<b>Возможные причины и рекомендации:</b>
1. Слишком узкие критерии поиска — попробуйте расширить регион или ключевые слова.
2. Сезонный спад активности в вашей сфере.
3. Торги еще не опубликованы, но могут появиться в ближайшие дни.

<b>Что можно сделать:</b>
• Расширить критерии поиска (например, добавить смежные отрасли).
• Ознакомиться с архивом завершенных торгов для анализа рынка.

Спасибо, что пользуетесь нашим сервисом!
"""
}

# =========== РАССЫЛКИ (из вашего файла) ===========
NEWSLETTERS = [
    {
        "title": "Самые частые причины отклонения заявок на участие в госзакупках",
        "content": """
🏛️ <b>Самые частые причины отклонения заявок на участие в госзакупках</b>

Как эксперты в сфере тендеров, мы ежедневно сталкиваемся с ситуациями, когда компании не допускаются к участию из-за простых ошибок. 

<b>Основные причины отказов:</b>
1. <b>Неполный пакет документов</b> – отсутствие необходимых лицензий или выписок
2. <b>Некорректное заполнение форм</b> – опечатки в реквизитах, неверные суммы
3. <b>Нарушение сроков подачи</b> – даже минута опоздания может стать фатальной
4. <b>Несоответствие требованиям ТЗ</b> – формальные расхождения в спецификациях

<b>Наша рекомендация:</b> Проведите аудит своей заявки перед отправкой или доверьте это профессионалам.

💡 <b>Нужна проверка вашей следующей заявки?</b> Ответьте «Проверка» — поможем бесплатно!
        """
    },
    {
        "title": "ТОП-5 мифов о работе с государственными закупками",
        "content": """
🎯 <b>ТОП-5 мифов о работе с государственными закупками</b>

Развеиваем популярные заблуждения, которые мешают бизнесу участвовать в тендерах:

<b>Миф 1:</b> «Тендерами занимаются только крупные компании»
<b>Реальность:</b> 44% победителей – малый и средний бизнес.

<b>Миф 2:</b> «Нужны большие деньги на обеспечение заявки»
<b>Реальность:</b> Существуют банковские гарантии и другие инструменты.

<b>Миф 3:</b> «Все тендеры уже «схвачены» заранее»
<b>Реальность:</b> 80% конкурсов проходят в честной борьбе.

<b>Миф 4:</b> «Процесс участия слишком сложный»
<b>Реальность:</b> При правильном сопровождении – это стандартная процедура.

<b>Миф 5:</b> «Мой бизнес слишком специфический для тендеров»
<b>Реальность:</b> Государство закупает абсолютно всё – от канцтоваров до IT-решений.

📞 <b>Есть вопросы по участию?</b> Пишите – проконсультируем!
        """
    },
    {
        "title": "Эффективные инструменты мониторинга государственных закупок",
        "content": """
🔍 <b>Эффективные инструменты мониторинга государственных закупок</b>

Самостоятельный поиск тендеров может отнимать до 15 часов в неделю. Рассказываем, как оптимизировать этот процесс:

<b>1. Специализированные площадки:</b>
• Сбербанк-АСТ, ЕЭТП, РТС-тендер
• Автоматические фильтры по вашим критериям

<b>2. Системы аналитики:</b>
• Контур.Закупки, СБИС Тендеры
• Отслеживание изменений в документации

<b>3. Наши рекомендации:</b>
• Настройте уведомления по ключевым словам
• Отслеживайте конкретных заказчиков
• Анализируйте историю закупок конкурентов

<b>Наш сервис делает это автоматически:</b> Мы ежедневно мониторим 50+ площадок и присылаем только релевантные предложения.

🚀 <b>Хотите получать персонализированные подборки?</b> Ответьте «Подборка» на это сообщение.
        """
    },
    {
        "title": "Юридический ликбез: что надо знать перед подачей жалобы в ФАС?",
        "content": """
⚖️ <b>Юридический ликбез: что надо знать перед подачей жалобы в ФАС?</b>

Подача жалобы в Федеральную антимонопольную службу – серьезный шаг. Вот что нужно знать:

<b>Основания для жалобы:</b>
1. Нарушение процедуры проведения закупки
2. Дискриминационные требования в документации
3. Необоснованное отклонение заявки

<b>Сроки:</b>
• Жалоба подается в течение 10 дней с момента нарушения
• Рассмотрение занимает до 5 рабочих дней

<b>Типичные ошибки:</b>
• Отсутствие доказательной базы
• Пропуск сроков подачи
• Некорректное оформление документов

<b>Наша практика:</b> В 73% случаев правильная жалоба приводит к пересмотру условий или восстановлению в процедуре.

🛡️ <b>Столкнулись с нарушениями?</b> Пришлите документацию – оценим шансы на успешное обжалование.
        """
    },
    {
        "title": "Какие изменения ожидают рынок госзакупок в следующем квартале?",
        "content": """
📈 <b>Какие изменения ожидают рынок госзакупок в следующем квартале?</b>

Анализируем тренды и готовимся к изменениям вместе с вами:

<b>1. Цифровизация:</b>
• Расширение применения электронных аккредитивов
• Внедрение блокчейн-технологий для контрактов

<b>2. Упрощение процедур:</b>
• Сокращение списка обязательных документов для МСП
• Расширение практики запроса котировок

<b>3. Новые требования:</b>
• Ужесточение контроля за субподрядчиками
• Обязательная экологическая отчетность

<b>4. Наши прогнозы:</b>
• Рост количества закупок у единственного поставщика
• Увеличение доли IT-тендеров на 15-20%

📊 <b>Хотите получать регулярные обзоры рынка?</b> Подпишитесь на нашу аналитическую рассылку – ответьте «Аналитика».
        """
    },
    {
        "title": "Практическое руководство: Как увеличить шансы на победу в конкурсе?",
        "content": """
🏆 <b>Практическое руководство: Как увеличить шансы на победу в конкурсе?</b>

На основе 100+ успешных кейсов делимся практическими советами:

<b>Этап 1: Подготовка (70% успеха)</b>
• Тщательный анализ ТЗ на предмет «подводных камней»
• Изучение заказчика и его предыдущих закупок
• Расчет реальной стоимости работ с учетом рисков

<b>Этап 2: Подача заявки</b>
• Тройная проверка всех документов
• Подготовка убедительного технического предложения
• Грамотное оформление финансовой части

<b>Этап 3: Подведение итогов</b>
• Мониторинг хода процедуры
• Готовность к запросам разъяснений
• Анализ результатов для будущих тендеров

<b>Наш опыт:</b> При комплексном сопровождении шансы на победу увеличиваются в 3-4 раза.

🎯 <b>Готовитесь к важному тендеру?</b> Давайте обсудим стратегию – напишите «Стратегия» в ответ.
        """
    }
]

# =========== КЛАВИАТУРЫ ===========
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету")],
            [KeyboardButton(text="📋 Мои заявки"), KeyboardButton(text="📨 Мои сообщения")],
            [KeyboardButton(text="ℹ️ Помощь"), KeyboardButton(text="📞 Контакты")]
        ],
        resize_keyboard=True
    )

def get_admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👥 Все клиенты"), KeyboardButton(text="🆕 Новые заявки")],
            [KeyboardButton(text="📤 Отправить тендер"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="📢 Рассылка"), KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="🏠 Главное меню")]
        ],
        resize_keyboard=True
    )

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отменить")]],
        resize_keyboard=True
    )

# =========== ОСНОВНЫЕ КОМАНДЫ ===========
@dp.message(Command("start"))
@dp.message(F.text == "🏠 Главное меню")
async def cmd_start(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            "👑 <b>Панель администратора</b>\n\n"
            "Доступные функции:\n"
            "• 👥 Все клиенты - список пользователей\n"
            "• 📤 Отправить тендер - отправить выгрузку клиенту\n"
            "• 📢 Рассылка - массовая отправка новостей\n"
            "• 📊 Статистика - аналитика работы",
            reply_markup=get_admin_keyboard()
        )
    else:
        await message.answer(
            "🏢 <b>Добро пожаловать в бот ООО 'Тритика'!</b>\n\n"
            "Я помогу вам найти подходящие тендеры.\n\n"
            "Нажмите <b>📝 Заполнить анкету</b>, чтобы начать поиск!",
            reply_markup=get_main_keyboard()
        )

# =========== ЗАПОЛНЕНИЕ АНКЕТЫ ===========
@dp.message(F.text == "📝 Заполнить анкету")
async def start_questionnaire(message: types.Message, state: FSMContext):
    await message.answer(
        "📋 <b>Начнем заполнение анкеты!</b>\n\n"
        "Как к вам обращаться?\n"
        "<i>Введите ваше ФИО:</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_name)

@dp.message(Questionnaire.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(full_name=message.text)
    await message.answer(
        f"✅ Приятно познакомиться, {message.text}!\n\n"
        "<b>Введите название вашей компании:</b>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_company)

@dp.message(Questionnaire.waiting_for_company)
async def process_company(message: types.Message, state: FSMContext):
    await state.update_data(company_name=message.text)
    await message.answer(
        "✅ Компания сохранена!\n\n"
        "<b>Введите ИНН компании:</b>\n"
        "<i>10 или 12 цифр</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_inn)

@dp.message(Questionnaire.waiting_for_inn)
async def process_inn(message: types.Message, state: FSMContext):
    await state.update_data(inn=message.text)
    await message.answer(
        "✅ ИНН сохранен!\n\n"
        "<b>Введите контактное лицо:</b>\n"
        "<i>Кто будет общаться по тендерам</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_contact)

@dp.message(Questionnaire.waiting_for_contact)
async def process_contact(message: types.Message, state: FSMContext):
    await state.update_data(contact_person=message.text)
    await message.answer(
        "✅ Контакт сохранен!\n\n"
        "<b>Введите телефон для связи:</b>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_phone)

@dp.message(Questionnaire.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    await state.update_data(phone=message.text)
    await message.answer(
        "✅ Телефон сохранен!\n\n"
        "<b>Введите email:</b>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_email)

@dp.message(Questionnaire.waiting_for_email)
async def process_email(message: types.Message, state: FSMContext):
    await state.update_data(email=message.text)
    await message.answer(
        "✅ Email сохранен!\n\n"
        "<b>Введите сферу деятельности (ОКВЭД):</b>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_activity)

@dp.message(Questionnaire.waiting_for_activity)
async def process_activity(message: types.Message, state: FSMContext):
    await state.update_data(activity_sphere=message.text)
    await message.answer(
        "✅ Сфера сохранена!\n\n"
        "<b>Введите ключевые слова для поиска:</b>\n"
        "<i>Чем занимается ваша компания</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_industry)

@dp.message(Questionnaire.waiting_for_industry)
async def process_industry(message: types.Message, state: FSMContext):
    await state.update_data(industry=message.text)
    await message.answer(
        "✅ Ключевые слова сохранены!\n\n"
        "<b>Введите бюджет контрактов:</b>\n"
        "<i>Например: 100 000 - 500 000 руб.</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_amount)

@dp.message(Questionnaire.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    await state.update_data(contract_amount=message.text)
    await message.answer(
        "✅ Бюджет сохранен!\n\n"
        "<b>Введите регионы работы:</b>\n"
        "<i>Где готовы выполнять контракты</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_regions)

@dp.message(Questionnaire.waiting_for_regions)
async def process_regions(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    created_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    # Сохраняем в базу
    conn = sqlite3.connect('tenders.db')
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO questionnaires 
    (user_id, username, full_name, company_name, inn, contact_person, phone, email, 
     activity_sphere, industry, contract_amount, regions, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        message.from_user.id,
        message.from_user.username,
        user_data['full_name'],
        user_data['company_name'],
        user_data['inn'],
        user_data['contact_person'],
        user_data['phone'],
        user_data['email'],
        user_data['activity_sphere'],
        user_data['industry'],
        user_data['contract_amount'],
        message.text,
        created_at
    ))
    
    # Добавляем в рассылку
    cursor.execute('''
    INSERT OR IGNORE INTO newsletter_subscribers (user_id, username, subscribed_at)
    VALUES (?, ?, ?)
    ''', (message.from_user.id, message.from_user.username, created_at))
    
    conn.commit()
    conn.close()
    
    # Отправляем подтверждение клиенту
    await message.answer(RESPONSE_TEMPLATES["request_received"], reply_markup=get_main_keyboard())
    
    # Уведомление админам
    admin_msg = f"""
    🆕 <b>НОВАЯ ЗАЯВКА #{cursor.lastrowid}</b>
    
    👤 <b>Клиент:</b> {user_data['full_name']}
    🏢 <b>Компания:</b> {user_data['company_name']}
    📞 <b>Телефон:</b> {user_data['phone']}
    📧 <b>Email:</b> {user_data['email']}
    
    💰 <b>Бюджет:</b> {user_data['contract_amount']}
    🌍 <b>Регионы:</b> {message.text}
    
    Для отправки выгрузки используйте:
    <code>/send_tender {message.from_user.id}</code>
    """
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_msg)
        except Exception as e:
            logger.error(f"Ошибка отправки админу {admin_id}: {e}")
    
    await state.clear()

# =========== АДМИН: ОТПРАВКА ВЫГРУЗКИ ===========
@dp.message(F.text == "📤 Отправить тендер")
async def admin_start_send_tender(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    await message.answer(
        "📤 <b>Отправка выгрузки тендеров клиенту</b>\n\n"
        "Введите ID пользователя:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminTender.waiting_for_user_id)

@dp.message(AdminTender.waiting_for_user_id)
async def admin_get_user_id(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        
        # Проверяем существование пользователя
        conn = sqlite3.connect('tenders.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM questionnaires WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,))
        user_data = cursor.fetchone()
        conn.close()
        
        if not user_data:
            await message.answer("❌ Пользователь не найден. Проверьте ID.")
            await state.clear()
            return
        
        await state.update_data(target_user_id=user_id)
        await message.answer(
            f"✅ Найден пользователь: {user_data[3]}\n"
            f"🏢 Компания: {user_data[4]}\n\n"
            f"<b>Введите название тендера:</b>"
        )
        await state.set_state(AdminTender.waiting_for_tender_title)
    except ValueError:
        await message.answer("❌ Введите корректный ID (число):")

@dp.message(AdminTender.waiting_for_tender_title)
async def admin_get_tender_title(message: types.Message, state: FSMContext):
    await state.update_data(tender_title=message.text)
    await message.answer("✅ Название сохранено!\n\n<b>Введите описание тендера:</b>")
    await state.set_state(AdminTender.waiting_for_tender_description)

@dp.message(AdminTender.waiting_for_tender_description)
async def admin_get_tender_description(message: types.Message, state: FSMContext):
    await state.update_data(tender_description=message.text)
    await message.answer("✅ Описание сохранено!\n\n<b>Введите ссылку на тендер:</b>")
    await state.set_state(AdminTender.waiting_for_tender_link)

@dp.message(AdminTender.waiting_for_tender_link)
async def admin_get_tender_link(message: types.Message, state: FSMContext):
    await state.update_data(tender_link=message.text)
    await message.answer("✅ Ссылка сохранена!\n\n<b>Введите бюджет/стоимость:</b>")
    await state.set_state(AdminTender.waiting_for_tender_amount)

@dp.message(AdminTender.waiting_for_tender_amount)
async def admin_get_tender_amount(message: types.Message, state: FSMContext):
    await state.update_data(tender_amount=message.text)
    await message.answer("✅ Сумма сохранена!\n\n<b>Введите срок подачи:</b>")
    await state.set_state(AdminTender.waiting_for_tender_deadline)

@dp.message(AdminTender.waiting_for_tender_deadline)
async def admin_send_tender(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = data['target_user_id']
    
    # Получаем данные пользователя
    conn = sqlite3.connect('tenders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM questionnaires WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,))
    user_data = cursor.fetchone()
    conn.close()
    
    if user_data:
        # Формируем выгрузку
        tender_export = RESPONSE_TEMPLATES["tender_export"].format(
            full_name=user_data[3],
            activity_sphere=user_data[9],
            regions=user_data[12],
            industry=user_data[10],
            created_at=user_data[14],
            tender_count="5+"  # Можно изменить на реальное количество
        )
        
        try:
            # Отправляем клиенту
            await bot.send_message(user_id, tender_export)
            
            # Сообщение о файле
            file_message = RESPONSE_TEMPLATES["file_sent"].format(
                filename=f"Тендеры_{datetime.now().strftime('%d.%m.%Y')}.pdf"
            )
            await bot.send_message(user_id, file_message)
            
            # Уведомляем админа
            await message.answer(
                f"✅ Выгрузка отправлена клиенту {user_data[3]}\n"
                f"📧 Email: {user_data[8]}\n"
                f"📞 Телефон: {user_data[7]}",
                reply_markup=get_admin_keyboard()
            )
        except Exception as e:
            await message.answer(f"❌ Ошибка отправки: {str(e)}", reply_markup=get_admin_keyboard())
    else:
        await message.answer("❌ Данные пользователя не найдены", reply_markup=get_admin_keyboard())
    
    await state.clear()

# =========== БЫСТРАЯ КОМАНДА АДМИНА ===========
@dp.message(Command("send_tender"))
async def quick_send_tender(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /send_tender ID_пользователя")
        return
    
    try:
        user_id = int(args[1])
        conn = sqlite3.connect('tenders.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM questionnaires WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,))
        user_data = cursor.fetchone()
        conn.close()
        
        if user_data:
            # Быстрая отправка стандартной выгрузки
            tender_export = RESPONSE_TEMPLATES["tender_export"].format(
                full_name=user_data[3],
                activity_sphere=user_data[9],
                regions=user_data[12],
                industry=user_data[10],
                created_at=user_data[14],
                tender_count="5+"
            )
            
            await bot.send_message(user_id, tender_export)
            await bot.send_message(user_id, RESPONSE_TEMPLATES["file_sent"].format(
                filename=f"Тендеры_{datetime.now().strftime('%d.%m.%Y')}.pdf"
            ))
            
            await message.answer(f"✅ Быстрая выгрузка отправлена клиенту {user_data[3]}")
        else:
            await message.answer("❌ Пользователь не найден")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

# =========== РАССЫЛКА ===========
async def send_newsletter():
    """Автоматическая рассылка каждые 2 недели"""
    conn = sqlite3.connect('tenders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username FROM newsletter_subscribers")
    subscribers = cursor.fetchall()
    conn.close()
    
    # Выбираем рассылку по очереди
    newsletter_index = get_newsletter_index()
    newsletter = NEWSLETTERS[newsletter_index]
    
    success_count = 0
    fail_count = 0
    
    for user_id, username in subscribers:
        try:
            await bot.send_message(
                user_id,
                f"📢 <b>{newsletter['title']}</b>\n\n{newsletter['content']}\n\n"
                f"<i>Команда ООО 'Тритика'</i>\n"
                f"📞 +7 (904) 653-69-87\n"
                f"🌐 https://tritika.ru/"
            )
            success_count += 1
            await asyncio.sleep(0.5)  # Задержка между отправками
        except Exception as e:
            fail_count += 1
            logger.error(f"Ошибка отправки рассылки пользователю {user_id}: {e}")
    
    # Уведомление админам
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📊 <b>Отчет по рассылке</b>\n\n"
                f"✅ Успешно отправлено: {success_count}\n"
                f"❌ Не отправлено: {fail_count}\n"
                f"📝 Тема: {newsletter['title']}"
            )
        except:
            pass

def get_newsletter_index():
    """Получаем индекс рассылки для текущего периода"""
    try:
        with open('newsletter_index.txt', 'r') as f:
            return int(f.read().strip())
    except:
        return 0

def save_newsletter_index(index):
    """Сохраняем индекс рассылки"""
    with open('newsletter_index.txt', 'w') as f:
        f.write(str(index))

# =========== ЗАПУСК БОТА ===========
async def on_startup():
    """Действия при запуске"""
    logger.info("✅ Бот запущен на Replit!")
    
    # Запускаем планировщик для рассылки каждые 2 недели
    scheduler.add_job(
        send_newsletter,
        CronTrigger(day_of_week='mon', hour=10, minute=0),  # Каждый понедельник в 10:00
        id='newsletter',
        replace_existing=True
    )
    scheduler.start()
    
    # Уведомляем админов
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, "🤖 Бот успешно запущен на Replit!")
        except:
            pass

async def main():
    # Запускаем фоновый сервер для keep-alive
    keep_alive()
    
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
