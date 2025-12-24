#!/usr/bin/env python3
"""
Простой запуск бота
"""
import subprocess
import sys

print("Установка зависимостей...")
subprocess.check_call([sys.executable, "-m", "pip", "install", "aiogram==3.0.0"])

print("Запуск бота...")
from bot import main
import asyncio

asyncio.run(main())
