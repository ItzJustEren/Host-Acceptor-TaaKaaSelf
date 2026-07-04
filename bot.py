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
message_format = 'plain'  # plain, bold, italic, underline
is_running = False
task = None
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
                        if code and str(code).strip():
                            logger.info(f"✅ Code received from Worker: {code}")
                            return str(code).strip()
                        else:
                            logger.info(f"⏳ No valid code yet (attempt {attempt}/{max_attempts})")
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

async def get_session_from_worker():
    """دریافت سشن ذخیره‌شده از Worker"""
    auth = base64.b64encode(f"{READER_NAME}:{READER_PASS}".encode()).decode()
    headers = {
        'Authorization': f'Basic {auth}',
        'X-Username': READER_NAME
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(CODER_URL, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('session_string')
    except Exception as e:
        logger.error(f"❌ Error getting session: {e}")
    return None

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

def apply_format(text, format_type):
    """اعمال فرمت به پیام"""
    if format_type == 'bold':
        return f"**{text}**"
    elif format_type == 'italic':
        return f"__{text}__"
    elif format_type == 'underline':
        return f"--{text}--"
    else:
        return text

# ============================================
# 📌 ربات اصلی
# ============================================
async def main():
    global client, session_string, target_chat, interval, message_text, message_format, is_running, task, login_attempts
    
    logger.info("🚀 Starting TaaKaa Bot on Render...")
    
    # 1. بررسی سشن ذخیره‌شده در Worker
    saved_session = await get_session_from_worker()
    if saved_session:
        logger.info("🔐 Using saved session from Worker...")
        try:
            client = TelegramClient(StringSession(saved_session), int(API_ID), API_HASH)
            await client.start()
            logger.info("✅ Logged in from saved session!")
            me = await client.get_me()
            logger.info(f"👤 Logged in as: {me.first_name} (@{me.username})")
        except Exception as e:
            logger.error(f"❌ Saved session failed: {e}")
            logger.info("📱 Falling back to code login...")
            await login_with_code()
    else:
        logger.info("📱 No saved session found. Starting login process...")
        await login_with_code()
    
    # 2. اگر لاگین نشدیم، خارج شو
    if client is None or not client.is_connected():
        logger.error("❌ Failed to login. Exiting.")
        return
    
    # 3. تعریف هندلرها (دستوری)
    @client.on(events.NewMessage(pattern='/start', outgoing=True))
    async def start_command(event):
        await event.respond(
            '🤖 **TaaKaa Self Bot Started!**\n\n'
            'Send chat ID/link (e.g. @mygroup) to start.\n'
            'Type `/panel` to see all commands.'
        )
    
    @client.on(events.NewMessage(pattern='/panel', outgoing=True))
    async def panel_command(event):
        help_text = (
            '🔧 **TaaKaa Self Bot Panel**\n\n'
            '**Commands:**\n'
            '`/start` - Start the bot\n'
            '`/stop` - Stop sending messages\n'
            '`/panel` - Show this panel\n'
            '`/status` - Show current settings\n'
            '`/ChangT <time>` - Change timer (e.g. `/ChangT 5m`, `/ChangT 30s`)\n'
            '`/ChangG <chat_id>` - Change target chat (e.g. `/ChangG @mygroup`)\n'
            '`/ChangM <message>` - Change message text\n'
            '`/TypeMsg` - Select message format (bold, italic, underline)\n'
            '`/Reset` - Reset all settings\n\n'
            '💡 All commands work in Saved Messages.'
        )
        await event.respond(help_text)
    
    @client.on(events.NewMessage(pattern='/status', outgoing=True))
    async def status_command(event):
        status_text = (
            f'📊 **Current Status**\n\n'
            f'📢 Target Chat: `{target_chat.title if target_chat else "Not set"}`\n'
            f'⏰ Timer: `{format_time(interval)}`\n'
            f'📝 Message: `{message_text}`\n'
            f'🎨 Format: `{message_format}`\n'
            f'🔄 Status: `{"✅ Running" if is_running else "⏸️ Stopped"}`'
        )
        await event.respond(status_text)
    
    @client.on(events.NewMessage(pattern='/ChangT', outgoing=True))
    async def change_timer(event):
        msg = event.message.text
        parts = msg.split(maxsplit=1)
        if len(parts) < 2:
            await event.respond('❌ Please provide time. Example: `/ChangT 5m`')
            return
        time_str = parts[1]
        seconds = parse_time_input(time_str)
        if seconds is None or seconds <= 0:
            await event.respond('❌ Invalid time format. Use: `5m`, `30s`, `500ms`')
            return
        global interval
        interval = seconds
        await event.respond(f'✅ Timer changed to: `{format_time(interval)}`')
    
    @client.on(events.NewMessage(pattern='/ChangG', outgoing=True))
    async def change_gap(event):
        msg = event.message.text
        parts = msg.split(maxsplit=1)
        if len(parts) < 2:
            await event.respond('❌ Please provide chat link or ID. Example: `/ChangG @mygroup`')
            return
        chat_input = parts[1]
        try:
            chat = await client.get_entity(chat_input)
            global target_chat
            target_chat = chat
            await event.respond(f'✅ Chat changed to: `{chat.title}`')
        except Exception as e:
            await event.respond(f'❌ Invalid chat! Error: {e}')
    
    @client.on(events.NewMessage(pattern='/ChangM', outgoing=True))
    async def change_message(event):
        msg = event.message.text
        parts = msg.split(maxsplit=1)
        if len(parts) < 2:
            await event.respond('❌ Please provide new message text. Example: `/ChangM Hello world!`')
            return
        new_message = parts[1]
        global message_text
        message_text = new_message
        await event.respond(f'✅ Message changed to: `{message_text}`')
    
    @client.on(events.NewMessage(pattern='/TypeMsg', outgoing=True))
    async def type_message(event):
        await event.respond(
            '🎨 **Select message format:**\n\n'
            'Type one of these commands:\n'
            '`/bold` - Bold text\n'
            '`/italic` - Italic text\n'
            '`/underline` - Underline text\n'
            '`/plain` - Plain text (default)'
        )
    
    @client.on(events.NewMessage(pattern='/bold', outgoing=True))
    async def set_bold(event):
        global message_format
        message_format = 'bold'
        await event.respond('✅ Message format set to: **Bold**')
    
    @client.on(events.NewMessage(pattern='/italic', outgoing=True))
    async def set_italic(event):
        global message_format
        message_format = 'italic'
        await event.respond('✅ Message format set to: __Italic__')
    
    @client.on(events.NewMessage(pattern='/underline', outgoing=True))
    async def set_underline(event):
        global message_format
        message_format = 'underline'
        await event.respond('✅ Message format set to: --Underline--')
    
    @client.on(events.NewMessage(pattern='/plain', outgoing=True))
    async def set_plain(event):
        global message_format
        message_format = 'plain'
        await event.respond('✅ Message format set to: Plain text')
    
    @client.on(events.NewMessage(pattern='/Reset', outgoing=True))
    async def reset_all(event):
        global target_chat, interval, message_text, message_format, is_running, task
        target_chat = None
        interval = 300
        message_text = 'Hello from TaaKaa!'
        message_format = 'plain'
        is_running = False
        if task:
            task.cancel()
        await event.respond('🔄 All settings reset to default.')
    
    @client.on(events.NewMessage(pattern='/stop', outgoing=True))
    async def stop_command(event):
        global is_running, task
        if is_running:
            is_running = False
            if task:
                task.cancel()
            await event.respond('⛔ Bot stopped! Send `/start` to restart.')
        else:
            await event.respond('⚠️ Bot is not running!')
    
    @client.on(events.NewMessage(outgoing=True))
    async def handle_messages(event):
        global target_chat, interval, message_text, is_running, task
        
        msg = event.message.text
        if not msg or msg.startswith('/'):
            return
        
        if target_chat is None:
            try:
                chat = await client.get_entity(msg)
                target_chat = chat
                await event.respond(f'✅ Chat "{chat.title}" saved!\n⏰ Send timer (e.g. `5m`):')
            except Exception as e:
                await event.respond(f'❌ Invalid chat! Try again:\n{e}')
        
        elif interval == 300 and target_chat is not None:
            seconds = parse_time_input(msg)
            if seconds is not None and seconds > 0:
                interval = seconds
                await event.respond(f'⏰ Timer: {format_time(interval)}\n✏️ Send message text:')
            else:
                await event.respond('❌ Invalid format! Use: `5m`, `30s`, `500ms`')
        
        elif message_text == 'Hello from TaaKaa!' and target_chat is not None and interval != 300:
            message_text = msg
            await event.respond(f'✅ Message saved!\n🚀 Starting...')
            
            if is_running and task:
                task.cancel()
            
            is_running = True
            task = asyncio.create_task(send_periodic())
            await event.respond(f'✅ Bot active!\n📤 Sending every {format_time(interval)}\n🛑 Send `/stop` to stop.')
    
    async def send_periodic():
        global is_running
        while is_running:
            try:
                if target_chat:
                    formatted_text = apply_format(message_text, message_format)
                    await client.send_message(target_chat, formatted_text, parse_mode='markdown')
                    logger.info(f'✅ Message sent to {target_chat.title} with format: {message_format}')
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
# 📌 تابع لاگین (با ترتیب درست: تلگرام → Worker)
# ============================================
async def login_with_code():
    global client, session_string, login_attempts
    
    # 1. ایجاد کلاینت و اتصال به تلگرام
    client = TelegramClient(StringSession(), int(API_ID), API_HASH)
    await client.connect()
    
    if not client.is_connected():
        logger.error("❌ Failed to connect to Telegram.")
        return
    
    logger.info("📱 Connected to Telegram. Requesting code...")
    
    try:
        # 2. درخواست کد از تلگرام (دستی)
        await client.send_code_request(PHONE_NUMBER)
        logger.info("✅ Code sent by Telegram! Check your phone or Telegram app.")
        logger.info("⏳ Now waiting for you to enter the code in Worker panel...")
        logger.info("🔄 I'll keep asking Worker every 3 seconds for up to 5 minutes.")
        
        # 3. منتظر بمون تا کد توی Worker ثبت بشه (حداکثر 5 دقیقه)
        code = await get_code_from_worker(max_attempts=100)
        if not code:
            logger.error("❌ No code received from Worker after 5 minutes. Exiting.")
            return
        
        # 4. کد رو به تلگرام بفرست
        try:
            await client.sign_in(PHONE_NUMBER, code)
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
                sys.exit(1)
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
        await asyncio.sleep(e.seconds + 5)
        sys.exit(1)
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
