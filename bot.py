# bot.py
import os
import json
import sqlite3
import hashlib
import secrets
from datetime import datetime
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn
import requests
import asyncio

# --- КОНФИГУРАЦИЯ (из переменных окружения) ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")      # https://bot1234.bothost.tech/webhook
WEBSOCKET_URL = os.getenv("WEBSOCKET_URL")  # wss://bot1234.bothost.tech/ws

MAX_API_URL = "https://platform-api2.max.ru"

app = FastAPI()

# Хранилище активных WebSocket соединений (логин -> websocket)
active_agents = {}

# --- БАЗА ДАННЫХ (SQLite) ---
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            secret_question TEXT NOT NULL,
            secret_answer_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")

def get_user(username):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT username, secret_question, secret_answer_hash FROM users WHERE username = ?', (username,))
    row = c.fetchone()
    conn.close()
    if row:
        return {'username': row[0], 'question': row[1], 'answer_hash': row[2]}
    return None

def register_user(username, question, answer):
    answer_hash = hashlib.sha256(answer.encode()).hexdigest()
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    try:
        c.execute('INSERT INTO users (username, secret_question, secret_answer_hash) VALUES (?, ?, ?)',
                 (username, question, answer_hash))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def check_answer(username, answer):
    user = get_user(username)
    if user:
        return hashlib.sha256(answer.encode()).hexdigest() == user['answer_hash']
    return False

# --- ФУНКЦИИ ДЛЯ ОТПРАВКИ СООБЩЕНИЙ В MAX ---
def send_message(chat_id, text, buttons=None):
    """Отправка сообщения через MAX API"""
    headers = {
        "Authorization": BOT_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {"chat_id": chat_id, "text": text}
    if buttons:
        payload["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]
    
    try:
        response = requests.post(f"{MAX_API_URL}/messages", headers=headers, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Ошибка отправки: {e}")
        return False

# --- WEBHOOK ДЛЯ ПРИЕМА СООБЩЕНИЙ ОТ MAX ---
@app.post("/webhook")
async def webhook(request: Request):
    """Прием сообщений от пользователей через Webhook"""
    try:
        data = await request.json()
        print(f"📩 Webhook получен: {data}")
        
        if data.get('event') == 'message_created':
            message = data.get('payload', {})
            chat_id = message.get('chat_id')
            text = message.get('text', '').strip()
            user_name = message.get('sender', {}).get('username', chat_id)
            
            print(f"📨 Сообщение от {user_name} ({chat_id}): {text}")
            
            if text.startswith('/start'):
                await handle_start(chat_id)
            elif text.startswith('/auth'):
                await handle_auth(chat_id, text)
            elif text.startswith('/status'):
                await handle_status(chat_id)
            else:
                await handle_unknown(chat_id)
    except Exception as e:
        print(f"Ошибка webhook: {e}")
    
    return {"status": "ok"}

async def handle_start(chat_id):
    """Команда /start"""
    text = """👋 Добро пожаловать в Парсер MAX!

Для начала работы нужно зарегистрироваться или войти.

📝 Если вы здесь впервые - придумайте логин и введите:
`/auth ваш_логин общий_пароль`

🔑 Если уже регистрировались - введите:
`/auth ваш_логин общий_пароль`

После авторизации вы сможете управлять локальным агентом."""
    
    buttons = [
        [{"type": "callback", "text": "📝 Регистрация", "payload": "register"}],
        [{"type": "callback", "text": "🔑 Вход", "payload": "login"}]
    ]
    send_message(chat_id, text, buttons)

async def handle_auth(chat_id, text):
    """Обработка авторизации"""
    parts = text.split()
    if len(parts) != 3:
        send_message(chat_id, "❌ Неправильный формат!\nИспользуйте: `/auth логин пароль`")
        return
    
    _, username, password = parts
    
    # Проверяем пароль
    if password != ADMIN_PASSWORD:
        send_message(chat_id, "❌ Неверный пароль. Обратитесь к администратору.")
        return
    
    user = get_user(username)
    
    if user:
        # Пользователь существует
        send_message(chat_id, f"❓ Контрольный вопрос: {user['question']}\n\nВведите ответ командой:\n`/answer ваш_ответ`")
    else:
        # Новый пользователь
        questions = {
            "1": "Девичья фамилия матери",
            "2": "Кличка питомца",
            "3": "Любимый город",
            "4": "Свой вариант"
        }
        text = "📝 Вы новый пользователь!\n\nВыберите контрольный вопрос:\n"
        for k, v in questions.items():
            text += f"{k}. {v}\n"
        text += "\nВведите номер вопроса командой:\n`/question 1`"
        send_message(chat_id, text)

async def handle_status(chat_id):
    """Проверка статуса агента"""
    if chat_id in active_agents:
        send_message(chat_id, "🟢 Агент подключен и готов к работе.")
    else:
        send_message(chat_id, "🔴 Агент не подключен.\nЗапустите агент на вашем ПК.")

async def handle_unknown(chat_id):
    send_message(chat_id, "❌ Неизвестная команда.\nИспользуйте /start для начала.")

# --- WEBSOCKET ДЛЯ АГЕНТОВ ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Подключение локальных агентов"""
    await websocket.accept()
    username = None
    
    try:
        # Ждем идентификацию
        data = await websocket.receive_text()
        auth_data = json.loads(data)
        
        if auth_data.get('action') != 'identify':
            await websocket.close()
            return
        
        username = auth_data.get('login')
        if not username:
            await websocket.close()
            return
        
        # Проверяем пользователя
        user = get_user(username)
        if not user:
            await websocket.send_text(json.dumps({"status": "error", "message": "Пользователь не найден"}))
            await websocket.close()
            return
        
        # Сохраняем соединение
        active_agents[username] = websocket
        print(f"✅ Агент подключен: {username}")
        await websocket.send_text(json.dumps({"status": "connected", "message": f"Добро пожаловать, {username}"}))
        
        # Слушаем сообщения от агента
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            
            if data.get('action') == 'log':
                log_text = data.get('message', '')
                send_message(username, f"📋 {log_text}")
            elif data.get('action') == 'status':
                status_text = data.get('message', '')
                send_message(username, f"📊 {status_text}")
                
    except WebSocketDisconnect:
        print(f"❌ Агент отключен: {username}")
        if username and username in active_agents:
            del active_agents[username]
    except Exception as e:
        print(f"Ошибка WebSocket: {e}")
        if username and username in active_agents:
            del active_agents[username]

# --- ВЕБ-ИНТЕРФЕЙС ---
@app.get("/")
async def index():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Парсер MAX</title>
        <meta charset="UTF-8">
        <style>
            body { font-family: Arial; max-width: 800px; margin: 50px auto; padding: 20px; }
            h1 { color: #333; }
            .status { padding: 15px; background: #e8f5e9; border-radius: 8px; }
        </style>
    </head>
    <body>
        <h1>🚗 Парсер MAX</h1>
        <div class="status">
            <p>✅ Бот работает</p>
            <p>📡 WebSocket: <code>wss://ваш_домен/ws</code></p>
        </div>
        <p>Используйте бота в MAX для управления.</p>
    </body>
    </html>
    """
    return HTMLResponse(html)

# --- ЗАПУСК ---
if __name__ == "__main__":
    init_db()
    print("🚀 Запуск бота...")
    print(f"📡 Webhook: {WEBHOOK_URL}")
    print(f"🔌 WebSocket: {WEBSOCKET_URL}")
    uvicorn.run(app, host="0.0.0.0", port=8000)
