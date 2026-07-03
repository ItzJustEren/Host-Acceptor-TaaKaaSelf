import asyncio
import os
import json
import base64
import aiohttp
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
import logging
import re

logging.basicConfig(level=logging.INFO)

# ============================================
# 📌 وب سرور کوچیک برای رندر (بدون Flask)
# ============================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"TaaKaa Bot is running!")

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    print(f"✅ Web server started on port {port}")
    server.serve_forever()

# اجرای وب سرور در یک ترد جداگانه
web_thread = threading.Thread(target=run_web_server, daemon=True)
web_thread.start()

# ============================================
# 📌 متغیرهای محیطی
# ============================================
READER_URL = os.environ.get('READER_URL', '')
READER_NAME = os.environ.get('READER_NAME', '')
READER_PASS = os.environ.get('READER_PASS', '')
PHONE_NUMBER = os.environ.get('PHONE_NUMBER', '')

if not READER_URL or not READER_NAME or not READER_PASS or not PHONE_NUMBER:
    print("❌ Error: All environment variables must be set!")
    print("   READER_URL, READER_NAME, READER_PASS, PHONE_NUMBER")
    exit(1)

# ============================================
# 📌 متغیرهای ربات
# ============================================
client = None
target_chat = None
interval = 300
message_text = 'Hello from TaaKaa!'
is_running = False
task = None
waiting_for = None
api_id = None
api_hash = None
session_string = None

# ============================================
# 📌 دریافت تنظیمات از Worker
# ============================================
async def get_config_from_worker():
    """هر 3 ثانیه از Worker اطلاعات می‌خواند تا زمانی که کامل شود."""
    auth = base64.b64encode(f"{READER_NAME}:{READER_PASS}".encode()).decode()
    headers = {
        'Authorization': f'Basic {auth}',
        'X-Username': READER_NAME
    }
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(READER_URL, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('ready') and 'api_id' in data and 'api_hash' in data and 'code' in data:
                            return data
                        elif not data.get('ready'):
                            print("⏳ Waiting for config to be set (not ready)...")
                        else:
                            print(f"⚠️ Config missing fields: {data}")
                    elif resp.status == 404:
                        print("⏳ Waiting for config to be set (404)...")
                    else:
                        print(f"⚠️ Worker responded with status: {resp.status}")
                    await asyncio.sleep(3)
            except Exception as e:
                print(f"❌ Error reading from Worker: {e}")
                await asyncio.sleep(5)

async def save_session_to_worker(session_str):
    """ذخیره سشن در Worker"""
    auth = base64.b64encode(f"{READER_NAME}:{READER_PASS}".encode()).decode()
    headers = {
        'Authorization': f'Basic {auth}',
        'X-Username': READER_NAME,
        'Content-Type': 'application/json'
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                READER_URL.replace('/get-config', '/save-session'),
                headers=headers,
                json={'session_string': session_str}
            ) as resp:
                if resp.status == 200:
                    print("✅ Session saved to Worker!")
                else:
                    print(f"⚠️ Failed to save session: {resp.status}")
        except Exception as e:
            print(f"❌ Error saving session: {e}")

# ============================================
# 📌 توابع کمکی
# ============================================
def parse_time_input(text):
    text = text.strip().lower()
    ms_match = re.match(r'^([\d.]+)\s*ms$', text)
    if ms_match:
        return float(ms_match.group(1)) / 1000
    s_match = re.match(r'^([\d.]+)\s*s$', text)
    if s_match:
        return float(s_match.group(1))
    m_match = re.match(r'^([\d.]+)\s*m$', text)
    if m_match:
        return float(m_match.group(1)) * 60
    try:
        return float(text) * 60
    except:
        return None

def format_time(seconds):
    if seconds < 1:
        return f"{seconds*1000:.0f} ms"
    elif seconds < 60:
        return f"{seconds:.1f} s"
    else:
        return f"{seconds/60:.1f} min"

# ============================================
# 📌 ربات اصلی
# ============================================
async def main():
    global client, api_id, api_hash, session_string, target_chat, interval, message_text, is_running, task
    
    print("🚀 Starting TaaKaa Bot on Render...")
    print(f"📡 Reader URL: {READER_URL}")
    print(f"👤 Reader Name: {READER_NAME}")
    print(f"📱 Phone: {PHONE_NUMBER}")
    
    # 1. دریافت تنظیمات از Worker
    config = await get_config_from_worker()
    api_id = int(config['api_id'])
    api_hash = config['api_hash']
    code = config['code']
    phone = config.get('phone_number', PHONE_NUMBER)
    saved_session = config.get('session_string')
    
    print(f"✅ Config received! API_ID: {api_id}")
    
    # 2. ایجاد کلاینت
    if saved_session:
        print("🔐 Using saved session...")
        client = TelegramClient(StringSession(saved_session), api_id, api_hash)
    else:
        print("📱 Creating new session...")
        client = TelegramClient(StringSession(), api_id, api_hash)
    
    # 3. لاگین
    try:
        if saved_session:
            await client.start()
            print("✅ Logged in from saved session!")
        else:
            await client.start(phone=phone, code_callback=lambda: code)
            print("✅ Logged in with new session!")
            
            # ذخیره سشن
            session_string = client.session.save()
            await save_session_to_worker(session_string)
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return
    
    # 4. اطلاعات کاربر
    me = await client.get_me()
    print(f"👤 Logged in as: {me.first_name} (@{me.username})")
    
    # 5. تعریف هندلرها
    @client.on(events.NewMessage(pattern='/start', outgoing=True))
    async def start_command(event):
        await event.respond('🤖 Bot started!\n\nSend chat ID/link (e.g. @mygroup):')
    
    @client.on(events.NewMessage(pattern='/panel', outgoing=True))
    async def panel_command(event):
        buttons = [
            [Button.inline("Developer", b"developer")],
            [Button.inline("Change Timer", b"change_timer")],
            [Button.inline("Change Gap", b"change_gap")],
            [Button.inline("Change Message", b"change_message")]
        ]
        await event.respond(
            "🔧 **TaaKaa Self Bot Panel**\n\n"
            "Select an option below:",
            buttons=buttons
        )
    
    @client.on(events.CallbackQuery)
    async def callback_handler(event):
        global target_chat, interval, message_text, is_running, task, waiting_for
        
        data = event.data.decode('utf-8')
        
        if data == "developer":
            await event.answer("Developer: @TaaKaaOrg", alert=True)
            await event.edit(
                "👨‍💻 **Developer Information**\n\n"
                "This bot is developed by **TaaKaa Organization**\n"
                "📱 Channel: @TaaKaaOrg\n"
                "🐙 GitHub: ItzJustEren/TaaKaa-Self\n\n"
                "💡 Follow us for updates!",
                buttons=[[Button.url("Visit Channel", "https://t.me/TaaKaaOrg")]]
            )
        
        elif data == "change_timer":
            await event.answer("⏰ Please send new timer")
            await event.edit(
                f"⏰ **Change Timer**\n\n"
                f"Current timer: `{format_time(interval)}`\n\n"
                "Please send the new timer in format:\n"
                "`1m` (1 minute), `30s` (30 seconds), `500ms` (500 milliseconds)"
            )
            waiting_for = "timer"
        
        elif data == "change_gap":
            await event.answer("📢 Please send new chat link")
            await event.edit(
                f"📢 **Change Chat/Gap**\n\n"
                f"Current chat: `{target_chat.title if target_chat else 'Not set'}`\n\n"
                "Please send the new chat link or ID:\n"
                "Example: `@mygroup` or `-1001234567890`"
            )
            waiting_for = "gap"
        
        elif data == "change_message":
            await event.answer("✏️ Please send new message")
            await event.edit(
                f"✏️ **Change Message**\n\n"
                f"Current message: `{message_text}`\n\n"
                "Please send the new message text:"
            )
            waiting_for = "message"
    
    @client.on(events.NewMessage(outgoing=True))
    async def handle_messages(event):
        global target_chat, interval, message_text, is_running, task, waiting_for
        
        msg = event.message.text
        if not msg:
            return
        
        # حالت انتظار
        if waiting_for == "timer":
            seconds = parse_time_input(msg)
            if seconds is not None and seconds > 0:
                interval = seconds
                await event.respond(f"✅ Timer changed to: `{format_time(interval)}`")
                waiting_for = None
            else:
                await event.respond("❌ Invalid format! Use: `1m`, `30s`, `500ms`")
            return
        
        elif waiting_for == "gap":
            try:
                chat = await client.get_entity(msg)
                target_chat = chat
                await event.respond(f"✅ Chat changed to: `{chat.title}`")
                waiting_for = None
            except Exception as e:
                await event.respond(f"❌ Invalid chat! Error: {e}")
            return
        
        elif waiting_for == "message":
            message_text = msg
            await event.respond(f"✅ Message changed to: `{message_text}`")
            waiting_for = None
            return
        
        if msg.startswith('/'):
            return
        
        # مرحله‌های /start
        if target_chat is None:
            try:
                chat = await client.get_entity(msg)
                target_chat = chat
                await event.respond(f'✅ Chat "{chat.title}" saved!\n⏰ Enter timer:')
            except Exception as e:
                await event.respond(f'❌ Invalid chat! Try again:\n{e}')
        
        elif interval == 300 and target_chat is not None:
            seconds = parse_time_input(msg)
            if seconds is not None and seconds > 0:
                interval = seconds
                await event.respond(f'⏰ Timer: {format_time(interval)}\n✏️ Enter message text:')
            else:
                await event.respond('❌ Invalid format! Use: 1m, 30s, 500ms')
        
        elif message_text == 'Hello from TaaKaa!' and target_chat is not None and interval != 300:
            message_text = msg
            await event.respond(f'✅ Message saved!\n🚀 Starting...')
            
            if is_running and task:
                task.cancel()
            
            is_running = True
            task = asyncio.create_task(send_periodic())
            await event.respond(f'✅ Bot active!\n📤 Sending every {format_time(interval)}\n🛑 Send /stop to stop.')
    
    @client.on(events.NewMessage(pattern='/stop', outgoing=True))
    async def stop_command(event):
        global is_running, task
        if is_running:
            is_running = False
            if task:
                task.cancel()
            await event.respond('⛔ Bot stopped! Send /start to restart.')
        else:
            await event.respond('⚠️ Bot is not running!')
    
    async def send_periodic():
        global is_running
        while is_running:
            try:
                if target_chat:
                    await client.send_message(target_chat, message_text)
                    print(f'✅ Message sent to {target_chat.title}')
            except Exception as e:
                print(f'❌ Error: {e}')
            
            if interval >= 1:
                for _ in range(int(interval)):
                    if not is_running:
                        break
                    await asyncio.sleep(1)
            else:
                await asyncio.sleep(interval)
    
    # 6. اجرا
    print("📝 Bot is running... Check Saved Messages")
    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Goodbye!")
    except Exception as e:
        print(f"❌ Fatal Error: {e}")
