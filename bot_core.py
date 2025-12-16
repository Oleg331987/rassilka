# В начале файла замените импорт
from database import SimpleDatabase as Database

# Инициализация
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db = Database()  # Без GitHub параметров
questionnaire = Questionnaire()

# Удалите функции send_broadcast_to_active_users и send_efficiency_report_to_admins
# или закомментируйте их, если они не используются

# В конце файла удалите эти функции или замените на простые заглушки
async def send_broadcast_to_active_users():
    """Заглушка для рассылки"""
    print("Рассылка отключена в упрощенной версии")
    return 0, 0

async def send_efficiency_report_to_admins():
    """Заглушка для отчета"""
    print("Отчет отключен в упрощенной версии")
