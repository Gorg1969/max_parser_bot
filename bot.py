import os
import json
import sqlite3
import hashlib
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
MAX_API_URL = "https://platform-api2.max.ru"

app = FastAPI()
active_agents = {}

# --- База данных (без изменений) ---
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        secret_question TEXT NOT NULL,
        secret_answer_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")

def get_user(username):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT username, secret_question, secret_answer_hash FROM users WHERE username = ?', (username,))
    row = c.fetchone()
    conn.close()
    return {'username': row[0], 'question': row[1], 'answer_hash': row[2]} if row else None

# --- WebSocket (оставляем, это главное) ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    username = None
    try:
        data = await websocket.receive_text()
        auth_data = json.loads(data)
        if auth_data.get('action') != 'identify':
            await websocket.close()
            return
        username = auth_data.get('login')
        if not username or not get_user(username):
            await websocket.send_text(json.dumps({"status": "error", "message": "Пользователь не найден"}))
            await websocket.close()
            return
        active_agents[username] = websocket
        print(f"✅ Агент подключен: {username}")
        await websocket.send_text(json.dumps({"status": "connected"}))
        while True:
            message = await websocket.receive_text()
            print(f"📨 От агента: {message}")
    except WebSocketDisconnect:
        if username and username in active_agents:
            del active_agents[username]
            print(f"❌ Агент отключен: {username}")

# --- Запуск ---
if __name__ == "__main__":
    init_db()
    print("🚀 Запуск бота (без Webhook)...")
    uvicorn.run(app, host="0.0.0.0", port=3000)
