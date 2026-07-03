import asyncio
import os
import sys
import base64
import aiohttp
import logging
import re
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberFloodError
)

# ============================================
# 📌 تنظیمات اولیه
# ============================================
try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ============================================
# 📌 وب سرور آسنکرون (برای Render)
# ============================================
async def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"✅ Starting async web server on port {port}")
    
    async def handler(reader, writer):
        try:
            await reader.read(1024)
            response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nTaaKaa Bot is running!"
            writer.write(response)
            await writer.drain()
        except Exception as e:
            logger.error(f"Web server error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()
    
    server = await asyncio.start_server(handler, '0.0.0.0', port)
    async with server:
        await server.serve_forever()

# ============================================
# 📌 متغیرهای محیطی
# ============================================
API_ID = os.environ.get('API_ID')
API_HASH = os.environ.get('API_HASH')
PHONE_NUMBER = os.environ.get('PHONE_NUMBER')
CODER_URL = os.environ.get('CODER_URL')
READER_NAME = os.environ.get('READER_NAME')
READER_PASS = os.environ.get('READER_PASS')
TWO_FA = os.environ.get('TWO_FA')  # اختیاری

if not all([API_ID, API_HASH, PHONE_NUMBER, CODER_URL, READER_NAME, READER_PASS]):
    logger.error("❌ All required environment variables must be set!")
    logger.error("   API_ID, API_HASH, PHONE_NUMBER, CODER_URL, READER_NAME, READER_PASS")
    sys.exit(1)

logger.info(f"📱 PHONE_NUMBER: {PHONE_NUMBER}")
logger.info(f"🔗 CODER_URL: {CODER_URL}")
logger.info(f"👤 READER_NAME: {READER_NAME}")
logger.info("🔐 TWO_FA: " + ("✅ Set" if TWO_FA else "❌ Not set (optional)"))

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
session_string = None
login_attempts = 0
MAX_LOGIN_ATTEMPTS = 3

# ============================================
# 📌 توابع ارتباط با Worker
# ============================================
async def get_code_from_worker(max_attempts=100):
    """هر 3 ثانیه از Worker کد را درخواست می‌کند تا زمانی که دریافت شود (حداکثر 100 بار = 5 دقیقه)."""
    auth = base64.b64encode(f"{READER_NAME}:{READER_PASS}".encode()).decode()
    headers = {
        'Authorization': f'Basic {auth}',
        'X-Username': READER_NAME
    }
    
    attempt = 0
    async with aiohttp.ClientSession() as session:
        while attempt < max_attempts:
            attempt += 1
            try:
                async with session.get(CODER_URL, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        code = data.get('code')
                        if code:
                            logger.info(f"✅ Code received from Worker: {code}")
                            return code
                        else:
                            logger.info(f"⏳ No code yet (attempt {attempt}/{max_attempts})")
                    elif resp.status == 404:
                        logger.info(f"⏳ Worker not ready (404) (attempt {attempt}/{max_attempts})")
                    else:
                        logger.warning(f"⚠️ Worker responded with status: {resp.status}")
                    await asyncio.sleep(3)
            except aiohttp.ClientError as e:
                logger.error(f"❌ Network error: {e}")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"❌ Unexpected error: {e}")
                await asyncio.sleep(5)
    
    logger.error(f"❌ Failed to get code from Worker after {max_attempts} attempts.")
    return None

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
            save_url = CODER_URL.replace('/get-code', '/save-session')
            async with session.post(save_url, headers=headers, json={'session_string': session_str}) as resp:
                if resp.status == 200:
                    logger.info("✅ Session saved to Worker!")
                else:
                    logger.warning(f"⚠️ Failed to save session: {resp.status}")
        except Exception as e:
            logger.error(f"❌ Error saving session: {e}")

async def reset_code_in_worker():
    """درخواست ریست کد به Worker"""
    auth = base64.b64encode(f"{READER_NAME}:{READER_PASS}".encode()).decode()
    headers = {
        'Authorization': f'Basic {auth}',
        'X-Username': READER_NAME,
        'Content-Type': 'application/json'
    }
    
    try:
        reset_url = CODER_URL.replace('/get-code', '/reset-code')
        async with aiohttp.ClientSession() as session:
            async with session.post(reset_url, headers=headers) as resp:
                if resp.status == 200:
                    logger.info("✅ Code reset in Worker!")
                    return True
                else:
                    logger.warning(f"⚠️ Failed to reset code: {resp.status}")
    except Exception as e:
        logger.error(f"❌ Error resetting code: {e}")
    return False

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
    global client, session_string, target_chat, interval, message_text, is_running, task, login_attempts
    
    logger.info("🚀 Starting TaaKaa Bot on Render...")
    
    # 1. بررسی سشن ذخیره‌شده در Worker
    try:
        auth = base64.b64encode(f"{READER_NAME}:{READER_PASS}".encode()).decode()
        headers = {
            'Authorization': f'Basic {auth}',
            'X-Username': READER_NAME
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(CODER_URL, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    saved_session = data.get('session_string')
                    if saved_session:
                        logger.info("🔐 Using saved session from Worker...")
                        client = TelegramClient(StringSession(saved_session), int(API_ID), API_HASH)
                        await client.start()
                        logger.info("✅ Logged in from saved session!")
                        me = await client.get_me()
                        logger.info(f"👤 Logged in as: {me.first_name} (@{me.username})")
                    else:
                        logger.info("📱 No saved session. Starting login process...")
                        await login_with_code()
                else:
                    logger.info("📱 Worker not ready. Starting login process...")
                    await login_with_code()
    except Exception as e:
        logger.error(f"❌ Error during login: {e}")
        return
    
    # 2. اگر لاگین نشدیم، خارج شو
    if client is None or not client.is_connected():
        logger.error("❌ Failed to login. Exiting.")
        return
    
    # 3. تعریف هندلرها
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
                    logger.info(f'✅ Message sent to {target_chat.title}')
            except FloodWaitError as e:
                logger.warning(f"⏳ Flood wait: {e.seconds} seconds. Increasing interval...")
                interval = e.seconds + 5
            except Exception as e:
                logger.error(f'❌ Error sending message: {e}')
            
            if interval >= 1:
                for _ in range(int(interval)):
                    if not is_running:
                        break
                    await asyncio.sleep(1)
            else:
                await asyncio.sleep(interval)
    
    logger.info("📝 Bot is running... Check Saved Messages")
    await client.run_until_disconnected()

# ============================================
# 📌 تابع لاگین (با درخواست کد از تلگرام، سپس دریافت از Worker)
# ============================================
async def login_with_code():
    global client, session_string, login_attempts
    
    # 1. ایجاد کلاینت و درخواست کد از تلگرام
    logger.info("📱 Requesting code from Telegram...")
    client = TelegramClient(StringSession(), int(API_ID), API_HASH)
    
    try:
        # این خط باعث میشه تلگرام کد رو به شماره شما بفرسته
        await client.start(phone=PHONE_NUMBER, code_callback=lambda: None)
        logger.info("✅ Code sent by Telegram! Waiting for you to enter it in Worker panel...")
        
        # 2. حالا منتظر بمون تا کد رو توی Worker ثبت کنی
        code = await get_code_from_worker(max_attempts=100)
        if not code:
            logger.error("❌ No code received from Worker after 5 minutes. Exiting.")
            return
        
        # 3. کد رو به تلگرام بفرست
        try:
            await client.sign_in(code=code)
            logger.info("✅ Logged in with new session!")
            session_string = client.session.save()
            await save_session_to_worker(session_string)
            login_attempts = 0  # ریست شمارنده در صورت موفقیت
            
            me = await client.get_me()
            logger.info(f"👤 Logged in as: {me.first_name} (@{me.username})")
            
        except PhoneCodeInvalidError:
            login_attempts += 1
            logger.error(f"❌ Invalid code. Attempt {login_attempts}/{MAX_LOGIN_ATTEMPTS}")
            await reset_code_in_worker()
            
            if login_attempts >= MAX_LOGIN_ATTEMPTS:
                logger.error("❌ Too many invalid attempts. Exiting.")
                return
            else:
                logger.info("⏳ Please enter a new code in Worker and restart the bot.")
                sys.exit(1)
                
        except SessionPasswordNeededError:
            if TWO_FA:
                logger.info("🔐 2FA required. Using provided password.")
                await client.sign_in(password=TWO_FA)
                logger.info("✅ Logged in with 2FA!")
                session_string = client.session.save()
                await save_session_to_worker(session_string)
            else:
                logger.error("❌ 2FA required but TWO_FA not set!")
                return
                
        except FloodWaitError as e:
            logger.error(f"⏳ Flood wait: {e.seconds} seconds. Please wait and try again.")
            return
            
    except FloodWaitError as e:
        logger.error(f"⏳ Telegram flood wait: {e.seconds} seconds. Please wait before trying again.")
        return
    except Exception as e:
        logger.error(f"❌ Login failed: {e}")
        return

# ============================================
# 📌 اجرای همزمان وب سرور و ربات
# ============================================
async def main_with_server():
    logger.info("🔥 Starting main function...")
    await asyncio.gather(
        run_web_server(),
        main()
    )

if __name__ == '__main__':
    try:
        asyncio.run(main_with_server())
    except KeyboardInterrupt:
        logger.info("👋 Goodbye!")
    except Exception as e:
        logger.error(f"❌ Fatal Error: {e}")
        sys.exit(1)
