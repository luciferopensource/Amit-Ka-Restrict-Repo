# -*- coding: utf-8 -*-
import os
import psutil
import time
import asyncio
# === Python 3.12+ Event Loop Compatibility ===
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import re
import shutil
import subprocess
import gc
import datetime
import uuid
import urllib.parse
from pathlib import Path
from collections import defaultdict
import motor.motor_asyncio
from pyrogram import Client, filters, enums, idle
from pyrogram.errors import (
    FloodWait, UserIsBlocked, InputUserDeactivated, UserAlreadyParticipant,
    InviteHashExpired, UsernameNotOccupied, FileReferenceExpired, UserNotParticipant,
    ApiIdInvalid, PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired,
    SessionPasswordNeeded, PasswordHashInvalid, PeerIdInvalid, AuthKeyUnregistered, UserDeactivated
)

from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, Message, 
    BotCommand, BotCommandScopeDefault, BotCommandScopeChat, CallbackQuery
)
from concurrent.futures import ThreadPoolExecutor
import sys

# --- LOGGER SETUP ---
class LoggerWriter:
    def __init__(self, is_stderr=False):
        self.terminal = sys.stderr if is_stderr else sys.stdout
        self.log = open("bot.log", "a", encoding="utf-8")
    def write(self, message):
        try:
            self.terminal.write(message)
        except Exception:
            try:
                enc = getattr(self.terminal, 'encoding', 'utf-8') or 'utf-8'
                self.terminal.write(message.encode(enc, errors='replace').decode(enc))
            except Exception:
                pass
        try:
            self.log.write(message)
            self.log.flush()
        except Exception:
            pass
    def flush(self):
        try:
            self.terminal.flush()
        except Exception:
            pass
        try:
            self.log.flush()
        except Exception:
            pass

# Redirect all prints and tracebacks to bot.log as well as the console
sys.stdout = LoggerWriter(is_stderr=False)
sys.stderr = LoggerWriter(is_stderr=True)


# ==============================================================================
# --- CONFIGURATION ---
# ==============================================================================

API_ID = int(os.environ.get("API_ID", "") or 0)
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DB_URI = os.environ.get("DB_URI", "")
DB_NAME = os.environ.get("DB_NAME", "")
STRING_SESSION = os.environ.get("STRING_SESSION", None)

# Error Log Channel (Optional)
LOG_CHANNEL = os.environ.get("LOG_CHANNEL", "") 

# ⏱ STATUS UPDATE TIME (Seconds) -> 10 Seconds par set hai
STATUS_UPDATE_INTERVAL = int(os.environ.get("STATUS_UPDATE_INTERVAL", 15))

# Queue System
TASK_QUEUE = defaultdict(list) 

# Create a thread pool for blocking tasks
io_executor = ThreadPoolExecutor(max_workers=4)

LOGIN_SYSTEM = os.environ.get("LOGIN_SYSTEM", "True").lower() == "true"
ERROR_MESSAGE = os.environ.get("ERROR_MESSAGE", "True").lower() == "true"
WAITING_TIME = int(os.environ.get("WAITING_TIME", 3))

# --- AUTHORIZATION & ADMIN CONFIGURATION ---
owner_str = os.environ.get("OWNER_ID", "").strip()
OWNER_ID = int(owner_str) if owner_str.isdigit() else 0

admin_str = os.environ.get("ADMINS", "").strip() or os.environ.get("ADMIN_ID", "").strip()
ADMINS = [int(x) for x in re.split(r'[,;\s]+', admin_str) if x.strip().isdigit()]

sudo_str = os.environ.get("SUDOS", "").strip()
SUDOS = [int(x) for x in re.split(r'[,;\s]+', sudo_str) if x.strip().isdigit()]

auth_str = os.environ.get("AUTH_USERS", "").strip()
AUTH_USERS_EXTRA = [int(x) for x in re.split(r'[,;\s]+', auth_str) if x.strip().isdigit()]

if OWNER_ID and OWNER_ID not in ADMINS:
    ADMINS.append(OWNER_ID)

# Master set of all authorized users (Owner, Admins, Sudos)
AUTH_USERS = set(ADMINS + SUDOS + AUTH_USERS_EXTRA)
if OWNER_ID:
    AUTH_USERS.add(OWNER_ID)

UNAUTH_NOTIFY_CACHE = {}  # user_id -> timestamp (throttle access denial notices)

def is_authorized(user_id: int) -> bool:
    if not user_id:
        return False
    if not AUTH_USERS:
        # If no admins or owner configured in environment, allow open access
        return True
    return user_id in AUTH_USERS

HELP_TXT = """<b>📚 BOT'S USAGE GUIDE</b>

▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬✘▬
<blockquote expandable>
<b>🟢 1. SINGLE & BATCH DOWNLOADS</b>
• Send a single link to process one post.
• Send links in a "From - To" format to process multiple files at once.
• Type <b>all</b> to download everything to the end of the channel!
• <b>Examples:</b>
  ├ <code>https://t.me/xxxx/1001</code>
  ├ <code>https://t.me/c/xxxx/101 - 120</code>
  └ <code>https://t.me/c/xxxx/1 - all</code>

<b>👀 2. LIVE WATCHERS (AUTO-FORWARDING)</b>
• Automatically monitor a source and forward new messages to targets.
• Supports routing to <b>Multiple Targets</b> simultaneously!
• Features built-in <b>Content Filtering</b> (e.g., Only Videos & Docs).
• <b>Setup:</b> Send <code>/watch https://t.me/channel/123</code>
• <b>Manage:</b> Use <code>/watchers</code> to view and delete mappings.

<b>🤖 3. BOT CHATS & RESTRICTED CONTENT</b>
• Send the link with <code>/b/</code>, the bot's username, and message ID.
• <b>Format:</b> <code>https://t.me/b/botusername/4321</code>
• Bypasses "Saving Restricted Content" limits automatically!

<b>🛠 4. USEFUL COMMANDS</b>
• <code>/dl</code> - Reply to a link to process it.
• <code>/watch</code> - Setup a new live auto-forwarder.
• <code>/watchers</code> (or <code>/list</code>) - View active watchers & filters.
• <code>/removetarget</code> - Remove a specific destination.
• <code>/removesource</code> (or <code>/unwatch</code>) - Stop watching a source.
• <code>/tasks</code> - Delete old progress and reload live progress pop-up.
• <code>/toggledl</code> - Turn ON/OFF Background Downloading.
• <code>/cancel</code> - Cancel ongoing tasks.
</blockquote>
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬✘▬"""

# ==============================================================================
# --- DATABASE ---
# ==============================================================================

class Database:
    def __init__(self, uri, database_name):
        if uri:
            self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
            self.db = self._client[database_name or "restrict_bot"]
            self.col = self.db.users
        else:
            self._client = None
            self.db = None
            self.col = None

    async def init_indexes(self):
        try:
            if not self.db:
                return
            await self.col.create_index("id", unique=True)
            await self.db.watchers.create_index([("source_id", 1), ("source_thread", 1)])
            await self.db.sync_progress.create_index([("user_id", 1), ("source_id", 1), ("dest_id", 1)])
            print("✅ Database indexes verified and initialized.")
        except Exception as e:
            print(f"⚠️ Index initialization notice: {e}")

    async def get_dl_status(self):
        setting = await self.db.settings.find_one({'id': 'system'})
        if setting: return setting.get('auto_download', True)
        return True

    async def toggle_dl_status(self):
        current = await self.get_dl_status()
        await self.db.settings.update_one({'id': 'system'}, {'$set': {'auto_download': not current}}, upsert=True)
        return not current

    def new_user(self, id, name):
        return dict(
            id = id,
            name = name,
            session = None,
            api_id = None,
            api_hash = None,
        )

    async def add_user(self, id, name):
        user = self.new_user(id, name)
        if not await self.is_user_exist(id):
            await self.col.insert_one(user)

    async def is_user_exist(self, id):
        user = await self.col.find_one({'id':int(id)})
        return bool(user)

    async def total_users_count(self):
        count = await self.col.count_documents({})
        return count

    async def get_all_users(self):
        cursor = self.col.find({})
        return cursor

    async def delete_user(self, user_id):
        await self.col.delete_many({'id': int(user_id)})

    async def set_session(self, id, session):
        await self.col.update_one({'id': int(id)}, {'$set': {'session': session}})

    async def get_session(self, id):
        user = await self.col.find_one({'id': int(id)})
        if user:
            return user.get('session')
        return None

    async def set_api_id(self, id, api_id):
        await self.col.update_one({'id': int(id)}, {'$set': {'api_id': api_id}})

    async def get_api_id(self, id):
        user = await self.col.find_one({'id': int(id)})
        if user:
            return user.get('api_id')
        return None

    async def set_api_hash(self, id, api_hash):
        await self.col.update_one({'id': int(id)}, {'$set': {'api_hash': api_hash}})

    async def get_api_hash(self, id):
        user = await self.col.find_one({'id': int(id)})
        if user:
            return user.get('api_hash')
        return None

    async def total_session_users_count(self):
        count = await self.col.count_documents({"session": {"$ne": None}})
        return count

    async def save_sync_progress(self, user_id, source_id, dest_id, msg_id):
        query = {"user_id": int(user_id), "source_id": str(source_id), "dest_id": str(dest_id)}
        await self.db.sync_progress.update_one(
            query,
            {"$set": {"last_msg_id": int(msg_id), "updated_at": datetime.datetime.now()}},
            upsert=True
        )

    async def get_sync_progress(self, user_id, source_id, dest_id):
        data = await self.db.sync_progress.find_one({"user_id": int(user_id), "source_id": str(source_id), "dest_id": str(dest_id)})
        return data.get("last_msg_id", 0) if data else 0

    async def add_watcher(self, user_id, source_id, dest_id, source_thread=None, dest_thread=None, delay=0, is_restricted=False, source_title=None, dest_title=None, allowed_types=None, text_format=None):
        if allowed_types is None:
            allowed_types = ["Video", "Document"] 
        if text_format is None:
            text_format = []

        query = {
            'source_id': int(source_id),
            'source_thread': int(source_thread) if source_thread else None
        }
        
        new_target = {
            "dest_id": int(dest_id),
            "dest_thread": int(dest_thread) if dest_thread else None,
            "dest_title": dest_title
        }

        existing = await self.db.watchers.find_one(query)
        if existing:
            targets = existing.get("targets", [])
            if not targets and 'dest_id' in existing:
                targets = [{"dest_id": existing['dest_id'], "dest_thread": existing.get('dest_thread'), "dest_title": existing.get('dest_title')}]
                
            target_exists = any(t["dest_id"] == new_target["dest_id"] and t.get("dest_thread") == new_target["dest_thread"] for t in targets)
            
            if not target_exists:
                targets.append(new_target)
                await self.db.watchers.update_one(query, {
                    "$set": {"targets": targets, "allowed_types": allowed_types, "delay": int(delay), "text_format": text_format}
                })
            else:
                await self.db.watchers.update_one(query, {"$set": {"allowed_types": allowed_types, "delay": int(delay), "text_format": text_format}})
        else:
            watcher = {
                "user_id": user_id,
                "source_id": int(source_id),
                "source_thread": int(source_thread) if source_thread else None,
                "delay": int(delay),
                "is_restricted": is_restricted,
                "source_title": source_title,
                "allowed_types": allowed_types,
                "text_format": text_format,
                "targets": [new_target],
                "created_at": datetime.datetime.now()
            }
            await self.db.watchers.insert_one(watcher)

    async def get_watcher(self, source_id, source_thread=None):
        query = {'source_id': int(source_id)}
        if source_thread:
            query['source_thread'] = int(source_thread)
        return await self.db.watchers.find_one(query)

    async def get_all_watchers(self):
        return self.db.watchers.find({})
        
    async def remove_watcher(self, source_id):
        result = await self.db.watchers.delete_many({'source_id': int(source_id)})
        return result.deleted_count > 0

    async def remove_watcher_target(self, source_id, dest_id, source_thread=None):
        query = {'source_id': int(source_id)}
        if source_thread is not None:
            query['source_thread'] = int(source_thread)
            
        result = await self.db.watchers.update_one(
            query,
            {"$pull": {"targets": {"dest_id": int(dest_id)}}}
        )
        
        doc = await self.db.watchers.find_one(query)
        if doc and not doc.get("targets"):
            await self.db.watchers.delete_one(query)
            
        return result.modified_count > 0

db = Database(DB_URI, DB_NAME)

# ==============================================================================
# --- CLIENT & GLOBAL STATE ---
# ==============================================================================

app = Client(
    "RestrictedBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=50,                 
    sleep_threshold=20,
    ipv6=False                  
)

# ==============================================================================
# --- NATIVE ASK IMPLEMENTATION ---
# ==============================================================================
ASK_FUTURES = {}

async def custom_ask(self, chat_id: int, text: str, filters=None, timeout: int = 300, **kwargs):
    msg = await self.send_message(chat_id, text, **kwargs)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    ASK_FUTURES[chat_id] = (future, filters)
    
    try:
        return await asyncio.wait_for(future, timeout)
    except asyncio.TimeoutError:
        ASK_FUTURES.pop(chat_id, None)
        
        class MockMessage:
            text = "/cancel"
            async def reply(self, reply_text, *args, **kw):
                return await self._client.send_message(chat_id, reply_text, *args, **kw)
                
        mock_msg = MockMessage()
        mock_msg._client = self
        return mock_msg

Client.ask = custom_ask

# ==============================================================================
# --- GLOBAL ACCESS CONTROL GATEKEEPERS (GROUP -2) ---
# ==============================================================================

@app.on_message(group=-2)
async def auth_gatekeeper(client: Client, message: Message):
    if not AUTH_USERS:
        return
    user_id = message.from_user.id if message.from_user else 0
    if not user_id:
        return
    if not is_authorized(user_id):
        message.stop_propagation()
        now = time.time()
        last_sent = UNAUTH_NOTIFY_CACHE.get(user_id, 0)
        if now - last_sent > 60:
            UNAUTH_NOTIFY_CACHE[user_id] = now
            try:
                await message.reply(
                    "⛔ <b>Access Denied!</b>\n\n"
                    "This is a private automation bot restricted strictly to authorized administrators.\n\n"
                    f"🆔 <b>Your User ID:</b> <code>{user_id}</code>\n"
                    "<i>Please contact the bot administrator if you require access.</i>",
                    quote=True
                )
            except Exception:
                pass

@app.on_callback_query(group=-2)
async def callback_auth_gatekeeper(client: Client, query: CallbackQuery):
    if not AUTH_USERS:
        return
    user_id = query.from_user.id if query.from_user else 0
    if not is_authorized(user_id):
        query.stop_propagation()
        try:
            await query.answer("⛔ Access Denied: You are not authorized to use this bot.", show_alert=True)
        except Exception:
            pass

@app.on_message(filters.private, group=-1)
async def ask_listener(client, message):
    chat_id = message.chat.id
    if chat_id in ASK_FUTURES:
        future, filter_cond = ASK_FUTURES[chat_id]
        
        if filter_cond:
            is_match = filter_cond(client, message)
            if asyncio.iscoroutine(is_match):
                is_match = await is_match
            if not is_match:
                return 
                
        ASK_FUTURES.pop(chat_id, None)
        if not future.done():
            future.set_result(message)
        message.stop_propagation()

# ==============================================================================

BOT_START_TIME = time.time()

ACTIVE_PROCESSES = defaultdict(dict)  
CANCEL_FLAGS = {}  

batch_temp = type("BT", (), {})()
batch_temp.ACTIVE_TASKS = defaultdict(int)

# --- ROBUST CONCURRENCY SETTINGS ---
SERVER_UPLOAD_LIMIT = asyncio.Semaphore(30) 
USER_SEMAPHORE_LIMIT = 3 
USER_SEMAPHORES = defaultdict(lambda: asyncio.Semaphore(USER_SEMAPHORE_LIMIT))

PENDING_TASKS = {}
PROGRESS = {}
SESSION_STRING_SIZE = 351

MAX_CONCURRENT_TASKS_PER_USER = int(os.environ.get("MAX_TASKS_PER_USER", "3"))

USER_CLIENTS = {} 

ALL_MSG_TYPES = ["Video", "Document", "Text", "Audio", "Photo", "Voice", "Animation", "Sticker"]

# ==============================================================================
# --- NEW PREMIUM PROGRESS UI AESTHETICS ---
# ==============================================================================

def _pretty_bytes(n: float) -> str:
    try:
        n = float(n)
    except Exception:
        return "0 B"
    if n == 0: return "0 B"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    unit = units[i]
    if unit == "B": return f"{int(n)} {unit}"
    else: return f"{n:.1f} {unit}"

def get_readable_time(seconds: int) -> str:
    try:
        seconds = int(seconds)
    except Exception:
        seconds = 0
    if seconds <= 0: return "0s"
    time_parts = []
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0: time_parts.append(f"{h}h")
    if m > 0: time_parts.append(f"{m}m")
    if not time_parts or s > 0: time_parts.append(f"{s}s")
    return " ".join(time_parts)

def generate_bar(percent: float, length: int = 11) -> str:
    filled_length = int(length * percent / 100)
    fraction = (percent / 100 * length) - filled_length
    has_half = fraction >= 0.5
    
    bar = '⬤' * filled_length
    if has_half and filled_length < length:
        bar += '◔'
        bar += '○' * (length - filled_length - 1)
    else:
        bar += '○' * (length - filled_length)
        
    return bar

def generate_aesthetic_progress_ui(task_info: dict, current_status: str = "Forwarding", percent: float = 0.0, footer_status: str = "FORWARDING") -> str:
    total = task_info.get("total", 0)
    success = task_info.get("success", 0)
    duplicate = task_info.get("duplicate", 0)
    deleted = task_info.get("deleted", 0)
    skipped = task_info.get("skipped", 0)
    filtered = task_info.get("filtered", 0)
    
    source_title = task_info.get("source_title", "Unknown Source")
    dest_title = task_info.get("dest_title_name", "Unknown Destination")
    
    if percent > 100.0: percent = 100.0

    bar_str = generate_bar(percent, length=11)

    text = (
        f"╔════❰ ғᴏʀᴡᴀʀᴅ sᴛᴀᴛᴜs ❱═❍⊱❁۪۪\n"
        f"║╭━━━━━━━━━━━━━━━➣\n"
        f"║┣⪼🕵 Tᴏᴛᴀʟ Msɢ : {total}\n"
        f"║┃\n"
        f"║┣⪼✅ sᴜᴄᴄᴇғᴜʟʟʏ Fᴡᴅ : {success}\n"
        f"║┃\n"
        f"║┣⪼👥 ᴅᴜᴘʟɪᴄᴀᴛᴇ Msɢ : {duplicate}\n"
        f"║┃\n"
        f"║┣⪼🗑 ᴅᴇʟᴇᴛᴇᴅ Msɢ : {deleted}\n"
        f"║┃\n"
        f"║┣⪼🪆 Sᴋɪᴘᴘᴇᴅ Msɢ : {skipped}\n"
        f"║┃\n"
        f"║┣⪼🔁 Fɪʟᴛᴇʀᴇᴅ Msɢ : {filtered}\n"
        f"║┃\n"
        f"║┣⪼📊 Cᴜʀʀᴇɴᴛ Sᴛᴀᴛᴜs: {current_status}\n"
        f"║┃\n"
        f"║┣⪼🔁 Source: {source_title}\n"
        f"║┃\n"
        f"║┣⪼📊 Destination : {dest_title}\n"
        f"║┃\n"
        f"║┣⪼𖨠 Pᴇʀᴄᴇɴᴛᴀɢᴇ: {percent:.1f} %\n"
        f"║╰━━━━━━━━━━━━━━━➣\n"
        f"╚════❰ {footer_status} ❱══❍⊱❁۪۪\n"
        f"〘{bar_str}〙 {percent:.1f}%"
    )
    return text

PROMO_NAMES_RAW = [
    "Cloud Station", "TamilMob", "LinkZz", "ONAAMovies", "Hdhub4u", "Cinevood", "Vegamovies", "Bollyflix", "1XBET", "Melbet", "Olamovies", "MoviesMod", "Khatrimaza", "9xmovies", "Desiremovies", "HubFlix", "Bolly4u", "Bolly2tolly", "Filmyzilla", "Tamilrockers", "JioRockers", "Kuttymovies", "Moviesda", "4kHdHub",
    "KatMovieHD", "KatMovies", "MoviesFlix", "MoviesVerse", "MoviesVerseHD", "MoviesRulz", "MovieRulz", "TamilYogi", "TamilMV", "TamilGun", "TamilBlasters", "TamilPrint", "Tamilian", "Isaimini", "KuttyWeb", "SSRMovies", "SSRMovie", "TeluguWap", "Ibomma", "Movierulz", "Filmypur", "Filmy4wap", "Filmywap", "FilmyMeet", "MoviesPapa", "MoviesBaba", "MoviesJoy", "MoviesNation", "MoviesCrown", "MoviesCounter", "MoviesDrive", "Moviezwap", "MoviezWap", "Mp4Moviez", "World4uFree", "9xflix", "DownloadHub", "DownloadHub4u", "HDMoviesHub", "HDMovieArea", "HDMovie14", "HDMovie2", "HubMovies", "BollyShare", "BollyRulez", "Bolly4Wap", "BollyZone", "BollyWoodMovies", "Cinevez", "CineHub", "Cineplex", "CineBinge", "FilmHub", "FilmBolly", "MovieBox", "MovieBoxPro", "MovieVerse", "MovieRaja", "MoviePanda", "MovieKing", "MoviePapa", "MovieMaza", "MovieDokan", "MoviezAdda", "Bollyflix4u", "Filmyzilla4u", "MoviesModHD", "LinkZ", "4KHDHub"
]

# Deduplicate promotional names case-insensitively
_seen_promo = set()
PROMO_NAMES = []
for _pname in PROMO_NAMES_RAW:
    _plower = _pname.lower()
    if _plower not in _seen_promo:
        _seen_promo.add(_plower)
        PROMO_NAMES.append(_pname)

PROMO_DOMAINS_SUFFIX = r"(?:\.(?:com|in|to|org|net|me|live|cc|vip|tv|nl|eu|is|pro|tech|info|xyz|app|site|club|link|top|world|online|hub|store|ws|biz|click|fun|trade|icu|cfd|lol))?"
PROMO_SPLIT_WORDS = [
    "cloud", "station", "tamil", "mob", "link", "zz", "onaa", "movies", "hd", "hub", "4u",
    "cine", "vood", "vega", "bolly", "flix", "1xbet", "melbet", "ola", "mod", "khatri", "maza",
    "9x", "desire", "2tolly", "filmy", "zilla", "rockers", "jio", "kutty", "da", "4k", "kat",
    "verse", "rulz", "yogi", "mv", "gun", "blasters", "print", "ian", "isaimini", "web",
    "ssr", "telugu", "wap", "ibomma", "pur", "meet", "papa", "baba", "joy", "nation", "crown",
    "counter", "drive", "zwap", "mp4", "world", "free", "area", "14", "2", "share", "zone",
    "wood", "vez", "plex", "binge", "box", "pro", "raja", "panda", "king", "dokan", "adda"
]

def _build_promo_regex():
    patterns = []
    for name in PROMO_NAMES:
        lower_name = name.lower().replace(" ", "")
        segments = []
        pos = 0
        while pos < len(lower_name):
            matched = False
            for w in sorted(PROMO_SPLIT_WORDS, key=len, reverse=True):
                if lower_name[pos:].startswith(w):
                    segments.append(re.escape(w))
                    pos += len(w)
                    matched = True
                    break
            if not matched:
                segments.append(re.escape(lower_name[pos]))
                pos += 1
        inner_pat = r'[-_.\s]*'.join(segments)
        pat = r'(?:\[|\(|\{)?@?' + inner_pat + PROMO_DOMAINS_SUFFIX + r'(?:\]|\)|\})?'
        patterns.append(pat)
    patterns.sort(key=len, reverse=True)
    return re.compile(r'(?<![a-zA-Z0-9])(?:' + '|'.join(patterns) + r')(?![a-zA-Z0-9])', re.IGNORECASE)

PROMO_NAME_REGEX = _build_promo_regex()

UNAMBIGUOUS_EXTS = r'mkv|mp4|webm|m4v|flv|wmv|m4a|flac|7z'
DELIMITED_ONLY_EXTS = r'avi|ts|avc|mp3|aac|wav|opus|ogg|zip|rar|tar|iso|apk|pdf'
ALL_MEDIA_EXTS = UNAMBIGUOUS_EXTS + r'|' + DELIMITED_ONLY_EXTS

# 1. Match split files (.001, .002, etc.) for ANY known extension, even if glued to letters/domains (e.g. Commkv.001)
SPLIT_EXT_REGEX = re.compile(
    r'^(?P<stem>.*?)(?:[\.\s_\-]+|(?<=[\)\]\}_>\'\"a-zA-Z0-9]))(?P<ext>' + ALL_MEDIA_EXTS + r')(?P<split>\.\d{2,4})(?P<closing>\s*(?:</[^>]+>)*\s*)$',
    re.IGNORECASE
)

# 2. Match ANY extension preceded by dot, space, underscore, hyphen, or bracket/quote
# E.g. .mkv,  mkv, _mkv, -mkv, ]mkv, .avi,  avi, .ts,  ts, .mp4,  mp4
DELIM_EXT_REGEX = re.compile(
    r'^(?P<stem>.*?)(?:[\.\s_\-]+|(?<=[\)\]\}_>\'\"]))(?P<ext>' + ALL_MEDIA_EXTS + r')(?P<closing>\s*(?:</[^>]+>)*\s*)$',
    re.IGNORECASE
)

# 3. Match unambiguous extensions even if glued directly to promo domain or letters (e.g. Commkv, Bollyflixmp4)
UNAMBIGUOUS_TAIL_REGEX = re.compile(
    r'^(?P<stem>.*?)(?P<ext>' + UNAMBIGUOUS_EXTS + r')(?P<closing>\s*(?:</[^>]+>)*\s*)$',
    re.IGNORECASE
)

# 4. Generic fallback with dot
GENERIC_EXT_REGEX = re.compile(
    r'^(?P<stem>.*?)(?P<ext>\.[a-zA-Z][a-zA-Z0-9]{1,4})(?P<split>\.\d{2,4})?(?P<closing>\s*(?:</[^>]+>)*\s*)$',
    re.IGNORECASE
)

def balance_html_tags(html: str) -> str:
    if not html:
        return ""
    tags = ['b', 'i', 'code', 'u', 's', 'pre']
    for tag in tags:
        open_count = len(re.findall(rf'<{tag}\b[^>]*>', html, re.IGNORECASE))
        close_count = len(re.findall(rf'</{tag}>', html, re.IGNORECASE))
        if open_count > close_count:
            html += f"</{tag}>" * (open_count - close_count)
    return html

def parse_media_extension_and_split(text: str):
    m = SPLIT_EXT_REGEX.search(text)
    if not m:
        m = DELIM_EXT_REGEX.search(text)
    if not m:
        m = UNAMBIGUOUS_TAIL_REGEX.search(text)
    if not m:
        m = GENERIC_EXT_REGEX.search(text)
    if m:
        stem = m.group("stem")
        ext = m.group("ext")
        if ext and not ext.startswith("."):
            ext = "." + ext.lower()
        split = m.groupdict().get("split") or ""
        closing = m.groupdict().get("closing") or ""
        if stem:
            # Strip trailing promotional domain residue from stem (e.g. .Com, .in, .org)
            stem = re.sub(r'(?i)\.(?:com|in|to|org|net|me|xyz|site|club|app|top|biz|info|cc)$', '', stem)
        return stem or "", ext or "", split or "", closing or ""
    return text, "", "", ""

# Quality regex: 2160p, 1080p, 720p, 480p, 360p, 1440p, 4k, 8k
QUALITY_REGEX = re.compile(r'\b(2160p|1080p|720p|480p|360p|1440p|4k|8k)\b', re.IGNORECASE)


# Hyperlink & domain regex for caption cleaning
LINK_PATTERN = re.compile(r'<a\s+[^>]*href=[\'"]([^\'"]+)[\'"][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
PROMO_DOMAINS = [
    r't\.me', r'telegram\.me', r'telegram\.dog', r'1xbet', r'melbet', r'vegamovies', r'bollyflix',
    r'hdhub4u', r'moviesmod', r'filmyzilla', r'khatrimaza', r'desiremovies', r'9xmovies',
    r'katmovie', r'moviesflix', r'moviesverse', r'movierulz', r'tamilmv', r'tamilrockers',
    r'isaimini', r'kuttyweb', r'ssrmovies', r'ibomma', r'filmypur', r'moviesdrive', r'mp4moviez',
    r'world4ufree', r'downloadhub', r'cinevez', r'cinehub', r'cinevood', r'linkzz', r'bit\.ly',
    r'tinyurl\.com', r'cutt\.ly', r'shorturl\.at'
]
PROMO_DOMAIN_REGEX = re.compile(r'https?://(?:[a-zA-Z0-9_-]+\.)*(?:' + '|'.join(PROMO_DOMAINS) + r')(?::\d+)?(?:/[^\s"\'>]*)?', re.IGNORECASE)
PROMO_PHRASES_REGEX = re.compile(r'\b(?:join|subscribe|telegram|channel|group|backup|click here|download now|direct link|update channel|official|powered by|credit|credits|uploaded by|shared by)\b', re.IGNORECASE)

PROMO_LINE_REGEX = re.compile(
    r'(?i)^\s*(?:[⚜️✨🌟👉🔹🔸🔻🔺➡️✔️📢🔗]\s*)*'
    r'(?:powered\s*by|uploaded\s*by|shared\s*by|posted\s*by|credits?|join|subscribe|telegram\s*channel|backup\s*channel|official\s*channel|main\s*channel|contact)'
    r'(?:\s*[:\-–—]\s*|\s+).*$',
    re.IGNORECASE
)

MD_LINK_PATTERN = re.compile(r'\[([^\]]+)\]\((https?://[^\)]+)\)')

def clean_promotional_hyperlinks(text_html: str) -> str:
    if not text_html:
        return ""

    # 1. Unwrap raw markdown links [anchor](url)
    def replace_md_link(match):
        anchor = match.group(1).strip()
        href = match.group(2).strip()
        plain_anchor = re.sub(r'<[^>]+>', '', anchor).strip()
        cleaned_anchor = clean_promotional_names(plain_anchor).strip()
        is_promo_anchor = (
            bool(PROMO_PHRASES_REGEX.search(plain_anchor)) or 
            bool(re.search(r'^@[a-zA-Z0-9_]+$', plain_anchor)) or
            cleaned_anchor == "" or
            bool(re.match(r'^[⚜️✨🌟👉🔹🔸🔻🔺➡️✔️📢🔗:\-\[\]\(\)\s\\/]*$', cleaned_anchor))
        )
        if is_promo_anchor:
            return ""
        return anchor

    text_html = re.compile(r'\[+([^\]]+)\]+\((https?://[^\)]+)\)').sub(replace_md_link, text_html)

    # 2. Unwrap HTML links <a href="url">anchor</a>
    def replace_html_link(match):
        href = match.group(1).strip()
        anchor = match.group(2).strip()
        plain_anchor = re.sub(r'<[^>]+>', '', anchor).strip()
        cleaned_anchor = clean_promotional_names(plain_anchor).strip()
        is_promo_anchor = (
            bool(PROMO_PHRASES_REGEX.search(plain_anchor)) or 
            bool(re.search(r'^@[a-zA-Z0-9_]+$', plain_anchor)) or
            cleaned_anchor == "" or
            bool(re.match(r'^[⚜️✨🌟👉🔹🔸🔻🔺➡️✔️📢🔗:\-\[\]\(\)\s\\/]*$', cleaned_anchor))
        )
        if is_promo_anchor:
            return ""
        return anchor

    cleaned = LINK_PATTERN.sub(replace_html_link, text_html)
    cleaned = PROMO_DOMAIN_REGEX.sub("", cleaned)
    cleaned = re.sub(r'(?i)\b(?:join|link|channel|backup channel|official channel)\s*:\s*', '', cleaned)
    return cleaned



def clean_promotional_names(text: str) -> str:
    if not text:
        return ""
    res = PROMO_NAME_REGEX.sub(" ", text)
    res = re.sub(r'(\.)\s*[\.\-_]\s*', r'\1', res)
    res = re.sub(r'([-_])\s*[-_]\s*', r'\1', res)
    res = re.sub(r'\s*-\s*(?=\.[a-zA-Z0-9])', '', res)
    res = re.sub(r'\s*\.\s*(?=\.[a-zA-Z0-9])', '', res)
    res = re.sub(r' {2,}', ' ', res)
    return res.strip()

def format_file_size(size_bytes: int | float) -> str:
    try:
        n = float(size_bytes)
    except Exception:
        return "0 B"
    if n <= 0:
        return "0 B"
    if n < 1024:
        return f"{int(n)} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.2f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.2f} MB"
    else:
        return f"{n / (1024 * 1024 * 1024):.2f} GB"

def append_media_footer(caption: str, file_size_bytes: int = 0) -> str:
    if not caption:
        caption = ""
    # Strip any pre-existing footer to prevent duplication across retries/fallbacks/targets
    caption = re.sub(r'\n*\s*💾\s*Size:[^\n]*', '', caption, flags=re.IGNORECASE)
    caption = re.sub(r'\n*\s*🔗\s*(?:Join|Link)[^\n]*', '', caption, flags=re.IGNORECASE)
    caption = re.sub(r'\n*\s*⚜️\s*Powered\s*By[^\n]*', '', caption, flags=re.IGNORECASE)
    caption = caption.strip()

    if file_size_bytes > 0:
        size_str = format_file_size(file_size_bytes)
        footer = f"\n\n💾 Size: {size_str}"
    else:
        footer = ""

    # Enforce Telegram 1024-character caption limit
    if len(caption) + len(footer) > 1024:
        avail = 1024 - len(footer)
        caption = caption[:avail].rstrip()

    return f"{caption}{footer}"

def prepare_caption_fallback(caption: str, file_size_bytes: int = 0) -> str:
    caption = re.sub(r'\n*\s*💾\s*Size:[^\n]*', '', caption, flags=re.IGNORECASE)
    caption = re.sub(r'\n*\s*🔗\s*(?:Join|Link)[^\n]*', '', caption, flags=re.IGNORECASE)
    caption = re.sub(r'\n*\s*⚜️\s*Powered\s*By[^\n]*', '', caption, flags=re.IGNORECASE)
    plain = re.sub(r'<[^>]+>', '', caption).strip()
    return append_media_footer(plain, file_size_bytes)


def sanitize_filename(filename: str) -> str:
    if not filename: return "unnamed_file.dat"
    filename = re.sub(r'[:]', "-", filename)
    filename = re.sub(r'[\\/*?"<>|]', "", filename)
    
    stem, ext, split_suffix, _ = parse_media_extension_and_split(filename)
    if not ext:
        ext = ".dat"

    # Replace underscores with spaces
    stem = stem.replace('_', ' ')

    # Remove promotional website/channel names
    stem = clean_promotional_names(stem)

    # Strip leading @usernames and leading promo brackets
    while True:
        old_stem = stem
        stem = re.sub(r'^\s*@[a-zA-Z0-9_]+\s*', '', stem)
        m_lead = re.match(r'^\s*(?:\[(.*?)\]|\((.*?)\))\s*', stem)
        if m_lead:
            bracket_content = m_lead.group(1) or m_lead.group(2) or ""
            if re.match(r'^(?:S\d+|E\d+|S\d+E\d+|Season\s*\d+|Episode\s*\d+|\d{4}|Hindi|English|Tamil|Telugu|Dual\s*Audio|Multi\s*Audio|HEVC|H\.?265|H\.?264|AAC|DDP|x264|x265|2160p|1080p|720p|480p|4K|8K)$', bracket_content.strip(), re.IGNORECASE):
                pass
            else:
                stem = stem[m_lead.end():]
        if old_stem == stem:
            break

    stem = re.sub(r'(@[a-zA-Z0-9_]+)\s*$', '', stem)

    has_quality = bool(QUALITY_REGEX.search(stem))
    if not has_quality and ext:
        stem = stem.rstrip(' .-')
        stem += " 720p"

    stem = stem.rstrip(' .-')
    final_name = f"{stem}{ext}{split_suffix}"
    final_name = re.sub(r' {2,}', ' ', final_name).strip()
    return final_name

def _process_media_line(line_html: str) -> tuple[str, bool]:
    stem, ext, split_suffix, closing_tags = parse_media_extension_and_split(line_html)
    if not ext:
        return line_html, False

    line_stem = stem.replace('_', ' ')
    line_stem = clean_promotional_names(line_stem)

    while True:
        old_stem = line_stem
        line_stem = re.sub(r'^((?:<[^>]+>)*\s*)@[a-zA-Z0-9_]+(\s*)', r'\1', line_stem)
        m_lead = re.match(r'^((?:<[^>]+>)*\s*)(?:\[(.*?)\]|\((.*?)\))(\s*)', line_stem)
        if m_lead:
            bracket_content = m_lead.group(2) or m_lead.group(3) or ""
            if re.match(r'^(?:S\d+|E\d+|S\d+E\d+|Season\s*\d+|Episode\s*\d+|\d{4}|Hindi|English|Tamil|Telugu|Dual\s*Audio|Multi\s*Audio|HEVC|H\.?265|H\.?264|AAC|DDP|x264|x265|2160p|1080p|720p|480p|4K|8K)$', bracket_content.strip(), re.IGNORECASE):
                pass
            else:
                line_stem = line_stem[:m_lead.start()] + m_lead.group(1) + line_stem[m_lead.end():]
        if old_stem == line_stem:
            break

    line_stem = re.sub(r'(@[a-zA-Z0-9_]+)(\s*(?:</[^>]+>)*\s*)$', r'\2', line_stem)

    if not QUALITY_REGEX.search(line_stem) and ext:
        line_stem = line_stem.rstrip(' .-')
        line_stem += " 720p"

    line_stem = line_stem.rstrip(' .-')
    final_line = f"{line_stem}{ext}{split_suffix}{closing_tags}"
    final_line = re.sub(r' {2,}', ' ', final_line).strip()
    return final_line, True

# 🚀 SMART CAPTION LOGIC
def smart_caption(text_html, file_size_bytes=0):
    if not text_html: return ""
    
    # 0. Clean hidden promotional links & URLs
    text_html = clean_promotional_hyperlinks(text_html)

    # 1. Remove existing footer if retried/fallback to prevent duplication
    text_html = re.sub(r'\n*\s*💾\s*Size:[^\n]*', '', text_html, flags=re.IGNORECASE)
    text_html = re.sub(r'\n*\s*🔗\s*(?:Join|Link)[^\n]*', '', text_html, flags=re.IGNORECASE)
    text_html = re.sub(r'\n*\s*⚜️\s*Powered\s*By[^\n]*', '', text_html, flags=re.IGNORECASE)
    text_html = text_html.strip()

    # 2. Process multiline captions preserving formatting
    lines = text_html.split('\n')
    transformed = False
    media_idx = -1

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        new_line, ok = _process_media_line(stripped)
        if ok:
            leading = line[:len(line) - len(line.lstrip())]
            lines[i] = leading + new_line
            transformed = True
            media_idx = i
            break

    if not transformed:
        new_text, ok = _process_media_line(text_html)
        if ok:
            lines = [new_text]
        else:
            lines = [clean_promotional_names(l.replace('_', ' ')) for l in lines]
    else:
        for j in range(len(lines)):
            if j != media_idx:
                lines[j] = clean_promotional_names(lines[j].replace('_', ' '))

    # Filter out promotional lines, raw @usernames, or empty punctuation lines
    filtered_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if filtered_lines and filtered_lines[-1] != "":
                filtered_lines.append("")
            continue

        plain = re.sub(r'<[^>]+>', '', stripped).strip()
        if not plain:
            continue

        # Match promo line regex on plain text and after stripping leading emoji/bracket decorations
        clean_prefix = re.sub(r'^[\[\(\s⚜️✨🌟👉🔹🔸🔻🔺➡️✔️📢🔗]+', '', plain)
        if PROMO_LINE_REGEX.match(plain) or PROMO_LINE_REGEX.match(clean_prefix):
            continue

        if PROMO_DOMAIN_REGEX.search(plain) or re.search(r'(?i)\b(?:t\.me|telegram\.me)\b', plain):
            continue

        if re.match(r'^\s*@[a-zA-Z0-9_]+\s*$', plain):
            continue

        cleaned_plain = clean_promotional_names(plain)
        if re.match(r'^[⚜️✨🌟👉🔹🔸🔻🔺➡️✔️📢🔗:\-\[\]\(\)\s\\/]*$', cleaned_plain):
            continue

        filtered_lines.append(line)

    result = '\n'.join(filtered_lines)
    result = re.sub(r' {2,}', ' ', result).strip()
    result = balance_html_tags(result)
    
    # 3. Append footer if file size provided
    if file_size_bytes > 0:
        result = append_media_footer(result, file_size_bytes)
        
    return result



async def check_link_restriction(user_id, link_text):
    clean_text = link_text.replace("https://", "").replace("http://", "").replace("t.me/", "").replace("c/", "")
    
    if "-" in clean_text:
        clean_text = clean_text.split("-")[0].strip()
        
    parts = clean_text.split("/")
    
    is_private = False
    chat_id = None
    msg_id = None

    try:
        if "t.me/b/" in link_text:
            return False, "🤖 **Bot Link:** Content availability depends on the bot."
            
        if "t.me/c/" in link_text:
            is_private = True
            chat_id = int("-100" + parts[0])
            if len(parts) > 1 and parts[-1].isdigit():
                msg_id = int(parts[-1])
        else:
            chat_id = parts[0]
            if len(parts) > 1 and parts[-1].isdigit():
                msg_id = int(parts[-1])
            
    except Exception as e:
        return None, f"⚠️ **Could not analyze link.** Error: {e}"

    is_temp_client = False
    check_client = app 
    
    if is_private:
        if user_id in USER_CLIENTS and USER_CLIENTS[user_id].is_connected:
            check_client = USER_CLIENTS[user_id]
            is_temp_client = False
        else:
            user_session = await db.get_session(user_id)
            if not user_session:
                return None, "🔒 **Private Link:** Please /login to verify restrictions."
            
            api_id = await db.get_api_id(user_id)
            api_hash = await db.get_api_hash(user_id)
            check_client = Client(f"check_{user_id}_{int(time.time())}", session_string=user_session, api_id=api_id, api_hash=api_hash, no_updates=True, ipv6=False, in_memory=True)
            is_temp_client = True

    is_restricted = False
    status_msg = ""
    
    try:
        if is_temp_client:
            await check_client.connect()
            
        if msg_id:
            msg = await check_client.get_messages(chat_id, msg_id)
            if getattr(msg.chat, "has_protected_content", False) or getattr(msg, "has_protected_content", False):
                is_restricted = True
                status_msg = "🔒 **Source is RESTRICTED** (Will use Download Mode)"
            else:
                is_restricted = False
                status_msg = "🔓 **Source is PUBLIC/UNRESTRICTED** (Will use Fast Forward)"
        else:
            chat = await check_client.get_chat(chat_id)
            if getattr(chat, "has_protected_content", False):
                is_restricted = True
                status_msg = "🔒 **Channel is RESTRICTED** (Will use Download Mode)"
            else:
                is_restricted = False
                status_msg = "🔓 **Channel is PUBLIC/UNRESTRICTED**"
            
    except Exception as e:
        if "CHANNEL_PRIVATE" in str(e) or "USER_NOT_PARTICIPANT" in str(e):
            status_msg = "⚠️ **Private Chat:** I can't check yet (You need to join first)."
        else:
            status_msg = f"⚠️ **Check Failed:** `{str(e)[:30]}...`"
    finally:
        if is_temp_client and check_client:
            try: await check_client.disconnect()
            except Exception: pass
        
    return is_restricted, status_msg
    
async def split_file_python(file_path, chunk_size=2000*1024*1024):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(io_executor, _split_file_smart, file_path, chunk_size)

def _split_file_smart(file_path, chunk_size):
    file_path = Path(file_path)
    if not file_path.exists():
        return []

    file_size = os.path.getsize(file_path)
    if file_size <= chunk_size:
        return [file_path]

    if shutil.which("split"):
        try:
            output_prefix = f"{file_path}.part"
            cmd = ["split", "-b", str(chunk_size), "-d", "-a", "3", str(file_path), output_prefix]
            subprocess.run(cmd, check=True, capture_output=True)
            parts = sorted(list(file_path.parent.glob(f"{file_path.name}.part*")))
            if parts: return parts
        except Exception: pass

    seven_z_exe = shutil.which("7z") or shutil.which("7za")
    if seven_z_exe:
        try:
            output_archive = f"{file_path}.7z"
            cmd = [seven_z_exe, "a", f"-v{chunk_size}b", "-mx0", output_archive, str(file_path)]
            subprocess.run(cmd, check=True, capture_output=True)
            parts = sorted(list(file_path.parent.glob(f"{file_path.name}.7z.*")))
            if parts: return parts
        except Exception: pass

    part_num = 0
    parts = []
    buffer_size = 32 * 1024 * 1024 
    
    with open(file_path, 'rb') as source:
        while True:
            part_name = file_path.parent / f"{file_path.name}.part{part_num:03d}"
            current_chunk_size = 0
            with open(part_name, 'wb') as dest:
                while current_chunk_size < chunk_size:
                    read_size = min(buffer_size, chunk_size - current_chunk_size)
                    data = source.read(read_size)
                    if not data: break
                    dest.write(data)
                    current_chunk_size += len(data)
            if current_chunk_size == 0:
                if part_name.exists(): part_name.unlink()
                break
            parts.append(part_name)
            part_num += 1
    return parts

def progress(current, total, task_uuid, typ):
    if task_uuid and CANCEL_FLAGS.get(task_uuid):
        raise Exception("CANCELLED_BY_USER")
    
    key = f"{task_uuid}:{typ}"
    now = time.time()
    if key not in PROGRESS:
        PROGRESS[key] = {
            "current": 0, "total": int(total), "percent": 0.0,
            "last_time": now, "last_current": 0, "speed": 0.0, "eta": None
        }
    rec = PROGRESS[key]
    rec["current"] = int(current)
    rec["total"] = int(total)
    if total > 0:
        rec["percent"] = (current / total) * 100.0
    dt = now - rec["last_time"]
    if dt >= 1 or current == total:
        delta_bytes = current - rec["last_current"]
        if dt <= 0: dt = 0.1
        speed = delta_bytes / dt
        rec["speed"] = speed
        rec["last_time"] = now
        rec["last_current"] = current
        if speed > 0 and total > current:
            rec["eta"] = (total - current) / speed
            
async def downstatus(client: Client, status_message: Message, chat, index: int, total_count: int, header_text: str = "", task_uuid: str = None, user_id: int = None):
    original_msg_id = status_message.id if status_message else 0
    original_chat_id = chat
    key = f"{task_uuid}:down" 
    last_text = ""
    while True:
        current_msg_id = original_msg_id
        current_chat_id = original_chat_id
        task_info = {}
        if task_uuid and user_id:
            task_info = ACTIVE_PROCESSES.get(user_id, {}).get(task_uuid, {})
            current_msg_id = task_info.get("status_msg_id", original_msg_id)
            current_chat_id = task_info.get("status_chat_id", original_chat_id)
            
        if task_uuid and CANCEL_FLAGS.get(task_uuid): break

        rec = PROGRESS.get(key)
        if not rec:
            await asyncio.sleep(1)
            continue
        if rec["current"] == rec["total"] and rec["total"] > 0:
            break
            
        percent = rec.get("percent", 0)
        speed_str = f"{_pretty_bytes(rec.get('speed', 0))}/s"
        size_str = f"{_pretty_bytes(rec.get('current', 0))} / {_pretty_bytes(rec.get('total', 0))}"
        eta_str = get_readable_time(int(rec.get('eta', 0)) if rec.get('eta') else 0)

        current_action = f"📥 Downloading ({percent:.1f}%)\n║┣⪼🚀 Sᴘᴇᴇᴅ: {speed_str}\n║┣⪼💾 Sɪᴢᴇ: {size_str}\n║┣⪼⏳ ETA: {eta_str}"
        status_text = generate_aesthetic_progress_ui(task_info, current_action, percent, "DOWNLOADING")

        if not task_info.get("confirming_cancel"):
            if status_text != last_text or task_info.get("force_update_ui"):
                task_info["force_update_ui"] = False
                try:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Task", callback_data=f"ask_cancel:{task_uuid}")]])
                    await client.edit_message_text(current_chat_id, current_msg_id, status_text, reply_markup=kb)
                    last_text = status_text
                except Exception:
                    pass
        
        total_size = rec.get("total", 0)
        if total_size > 0 and total_size < 50 * 1024 * 1024:
            await asyncio.sleep(8) 
        else:
            await asyncio.sleep(15)
            
async def upstatus(client: Client, status_message: Message, chat, index: int, total_count: int, header_text: str = "", task_uuid: str = None, user_id: int = None):
    original_msg_id = status_message.id if status_message else 0
    original_chat_id = chat
    key = f"{task_uuid}:up"
    last_text = ""
    while True:
        current_msg_id = original_msg_id
        current_chat_id = original_chat_id
        task_info = {}
        if task_uuid and user_id:
            task_info = ACTIVE_PROCESSES.get(user_id, {}).get(task_uuid, {})
            current_msg_id = task_info.get("status_msg_id", original_msg_id)
            current_chat_id = task_info.get("status_chat_id", original_chat_id)
            
        if task_uuid and CANCEL_FLAGS.get(task_uuid): break
        
        rec = PROGRESS.get(key)
        if not rec:
            await asyncio.sleep(1)
            continue
        if rec["current"] == rec["total"] and rec["total"] > 0:
            break
            
        percent = rec.get("percent", 0)
        speed_str = f"{_pretty_bytes(rec.get('speed', 0))}/s"
        size_str = f"{_pretty_bytes(rec.get('current', 0))} / {_pretty_bytes(rec.get('total', 0))}"
        eta_str = get_readable_time(int(rec.get('eta', 0)) if rec.get('eta') else 0)

        current_action = f"📤 Uploading ({percent:.1f}%)\n║┣⪼🚀 Sᴘᴇᴇᴅ: {speed_str}\n║┣⪼💾 Sɪᴢᴇ: {size_str}\n║┣⪼⏳ ETA: {eta_str}"
        status_text = generate_aesthetic_progress_ui(task_info, current_action, percent, "UPLOADING")

        if not task_info.get("confirming_cancel"):
            if status_text != last_text or task_info.get("force_update_ui"):
                task_info["force_update_ui"] = False
                try:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Task", callback_data=f"ask_cancel:{task_uuid}")]])
                    await client.edit_message_text(current_chat_id, current_msg_id, status_text, reply_markup=kb)
                    last_text = status_text
                except Exception:
                    pass
        
        total_size = rec.get("total", 0)
        if total_size > 0 and total_size < 50 * 1024 * 1024:
            await asyncio.sleep(8) 
        else:
            await asyncio.sleep(15)
            
def get_message_type(msg: Message):
    if msg.document: return "Document"
    if msg.video: return "Video"
    if msg.animation: return "Animation"
    if msg.sticker: return "Sticker"
    if msg.voice: return "Voice"
    if msg.audio: return "Audio"
    if msg.photo: return "Photo"
    if msg.text: return "Text"
    return None

# ==============================================================================
# --- HANDLERS ---
# ==============================================================================

async def test_destination_access(client: Client, chat_id, thread_id=None):
    try:
        test_msg = await client.send_message(
            chat_id=chat_id,
            text="🔄 **Testing Destination Accessibility...**\n*(This message will self-destruct)*",
            reply_to_message_id=thread_id
        )
        await test_msg.delete()
        return True, "Success"
    except Exception as e:
        return False, str(e)
        
@app.on_message(filters.command(["start"]) & (filters.private | filters.group))
async def send_start(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_authorized(user_id):
        return await message.reply(f'⛔ <b>Access Denied!</b>\n\nThis bot is private and restricted to authorized administrators.\n\n🆔 <b>Your User ID:</b> <code>{user_id}</code>', quote=True)
    user_name = message.from_user.first_name
    
    try:
        if not await db.is_user_exist(user_id):
            await db.add_user(user_id, user_name)
            print(f"New user {user_id} saved to database.") 
    except Exception as e:
        print(f"Failed to save user {user_id}: {e}")

    welcome_video_url = "https://holy-field-055f.lucifermotivational.workers.dev/0:/Google%20Flow%20%E2%80%93%20Sept%2004%20-%2016-28.mp4"
    welcome_text = (
        f"<b>👋 Hi {message.from_user.mention}, I am Save Restricted Content Bot.</b>\n\n"
        "<b>For downloading restricted content /login first.</b>\n\n"
        "<b>Know how to use bot by - /help</b>"
    )
    
    buttons = [
        [InlineKeyboardButton("❣️ Developer", url = "https://t.me/LuciferOpenSource")],
        [InlineKeyboardButton('🔍 sᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ', url='https://t.me/LuciferOpenSourceDiscussionGroup'), InlineKeyboardButton('🤖 ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ', url='https://t.me/LuciferOpenSource')]
    ]

    try:
        await client.send_video(
            chat_id=message.chat.id, 
            video=welcome_video_url, 
            caption=welcome_text, 
            reply_markup=InlineKeyboardMarkup(buttons),
            reply_to_message_id=message.id
        )
    except Exception as e:
        await client.send_message(
            chat_id=message.chat.id,
            text=welcome_text,
            reply_markup=InlineKeyboardMarkup(buttons),
            reply_to_message_id=message.id
        )

@app.on_message(filters.command(["help"]) & (filters.private | filters.group))
async def send_help(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if not is_authorized(user_id):
        return await message.reply('⛔ <b>Access Denied:</b> You are not authorized to use this bot.', quote=True)
    await client.send_message(
        message.chat.id, 
        text=HELP_TXT,
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True
    )

@app.on_message(filters.command(["toggledl"]) & (filters.user(ADMINS) | filters.user(SUDOS)))
async def toggle_dl_command(client: Client, message: Message):
    new_status = await db.toggle_dl_status()
    if new_status:
        await message.reply("✅ **Auto-Download is now ON.**\nBot will download files if direct fast-copy fails.")
    else:
        await message.reply("🚫 **Auto-Download is now OFF.**\nBot will ONLY direct-forward. If forwarding fails (restricted or floodwait), it will simply SKIP the file to save bandwidth.")

@app.on_message(filters.command(["tasks", "progress"]) & filters.private)
async def task_refresh_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_authorized(user_id):
        return await message.reply('⛔ <b>Access Denied:</b> You are not authorized to use this bot.', quote=True)
    user_tasks = ACTIVE_PROCESSES.get(user_id, {})
    if not user_tasks:
        return await message.reply("💤 **No active tasks running.**")
        
    for task_uuid, info in list(user_tasks.items()):
        if CANCEL_FLAGS.get(task_uuid): continue
        info["needs_refresh"] = True
    await message.reply("✅ **Refreshing... Your live progress message will pop up again shortly!**")

@app.on_message(filters.command(["cancel"]) & (filters.private | filters.group))
async def send_cancel(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_authorized(user_id):
        return await message.reply('⛔ <b>Access Denied:</b> You are not authorized to use this bot.', quote=True)

    if user_id in PENDING_TASKS:
        del PENDING_TASKS[user_id]
        await message.reply("✅ **Setup process cancelled.** You can send a new link now.")
        return

    user_tasks = ACTIVE_PROCESSES.get(user_id, {})
    if not user_tasks:
        await message.reply("✅ **No active tasks to cancel.**\n\n*(Note: To remove a Watcher, please use the /watchers command and click Delete, or use /unwatch)*")
        return

    buttons = []
    for tid, info in list(user_tasks.items()):
        label = info.get("item", "Task")
        label_short = (label[:26] + "...") if len(label) > 29 else label
        buttons.append([InlineKeyboardButton(f"🛑 {label_short}", callback_data=f"ask_cancel:{tid}")])
    buttons.append([InlineKeyboardButton("🛑 Cancel ALL My Tasks", callback_data="ask_cancel_all")])
    buttons.append([InlineKeyboardButton("❌ Close Menu", callback_data="close_menu")])

    await message.reply(
        "**🚫 Cancel Tasks**\n\nSelect the task you want to cancel:",
        reply_markup=InlineKeyboardMarkup(buttons),
        quote=True
    )
    
@app.on_callback_query(filters.regex(r"^cancel_") | filters.regex(r"^cancel_task:") | filters.regex(r"^ask_cancel") | filters.regex(r"^resume_task:"))
async def cancel_callback(client: Client, query):
    user_id = query.from_user.id
    data = query.data

    if data == "cancel_setup":
        if user_id in PENDING_TASKS:
            del PENDING_TASKS[user_id]
        await query.message.edit("❌ **Task Setup Cancelled.**")
        return

    if data == "ask_cancel_all":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ YES, CANCEL ALL", callback_data="cancel_all")],
            [InlineKeyboardButton("❌ NO, GO BACK", callback_data="close_menu")]
        ])
        await query.message.edit("**⚠️ Are you sure you want to cancel ALL active tasks?**", reply_markup=kb)
        return

    if data == "cancel_all":
        if user_id in TASK_QUEUE:
            TASK_QUEUE[user_id].clear()
        
        user_tasks = list(ACTIVE_PROCESSES.get(user_id, {}).keys())
        if not user_tasks:
            await query.answer("No active tasks to cancel.", show_alert=True)
            try: await query.message.delete()
            except Exception: pass
            return
        for tid in user_tasks:
            CANCEL_FLAGS[tid] = True 
        await query.message.edit("**🛑 Cancelling ALL your tasks...**\n(This may take a moment to stop current downloads)")
        return

    if data.startswith("ask_cancel:"):
        task_uuid = data.split(":",1)[1]
        user_tasks = ACTIVE_PROCESSES.get(user_id, {})
        if task_uuid not in user_tasks:
            await query.answer("Task not found or already finished.", show_alert=True)
            try: await query.message.delete()
            except Exception: pass
            return
            
        user_tasks[task_uuid]["confirming_cancel"] = True
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ YES, CANCEL", callback_data=f"cancel_task:{task_uuid}")],
            [InlineKeyboardButton("❌ NO, RESUME", callback_data=f"resume_task:{task_uuid}")]
        ])
        await query.message.edit("⚠️ **Are you sure you want to cancel this task?**\n\n*(Background progress will pause updating here until you choose)*", reply_markup=kb)
        return

    if data.startswith("resume_task:"):
        task_uuid = data.split(":",1)[1]
        user_tasks = ACTIVE_PROCESSES.get(user_id, {})
        if task_uuid in user_tasks:
            user_tasks[task_uuid]["confirming_cancel"] = False
            user_tasks[task_uuid]["force_update_ui"] = True
            await query.answer("▶️ Task Resumed!", show_alert=False)
            if query.message.id != user_tasks[task_uuid].get("status_msg_id"):
                try: await query.message.delete()
                except Exception: pass
        else:
            await query.answer("Task already finished.", show_alert=True)
            try: await query.message.delete()
            except Exception: pass
        return

    if data.startswith("cancel_task:"):
        task_uuid = data.split(":",1)[1]
        user_tasks = ACTIVE_PROCESSES.get(user_id, {})
        if task_uuid not in user_tasks:
            await query.answer("Task not found or already finished.", show_alert=True)
            try: await query.message.delete()
            except Exception: pass
            return
        CANCEL_FLAGS[task_uuid] = True
        await query.message.edit(f"🛑 **Task cancelled:** `{user_tasks[task_uuid].get('item','Task')}`\nIt will stop shortly.")
        return
        
@app.on_callback_query(filters.regex("^close_menu"))
async def close_menu(client, query):
    try:
        await query.message.delete()
    except Exception:
        await query.answer("Menu closed.")

@app.on_message(filters.command(["pixel"]) & (filters.user(ADMINS) | filters.user(SUDOS)))
async def pixel_bypass_handler(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply(
            "**Usage:**\n`/pixel https://pixeldrain.com/u/xxxx`\n\n"
            "Or multiple comma-separated links:\n"
            "`/pixel link1,link2,link3`"
        )

    input_text = message.text.split(None, 1)[1]
    matches = re.findall(r"pixeldrain\.com/u/([a-zA-Z0-9_-]+)", input_text)
    
    if not matches:
        return await message.reply(
            "❌ **No valid Pixeldrain links found.**\n"
            "Please ensure the links follow the format: `https://pixeldrain.com/u/XXXX`"
        )

    lines = []
    for match in matches:
        orig = f"https://pixeldrain.com/u/{match}"
        byp = f"https://cdn.pixeldrain.eu.cc/{match}"
        lines.append(f"🔗 **Original:** {orig}\n🔓 **Bypassed:** `{byp}`\n")
        
    bypassed_text = "\n".join(lines)
    
    reply_text = (
        "✨ **Pixeldrain Bypass Successful!** ✨\n\n"
        "**📥 Bypassed Links:**\n\n"
        f"{bypassed_text}\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        "🌐 **Original Bypass Website:** [Click Here](https://pixeldrain-bypass.gamedrive.org/)\n"
        "📜 **Userscript:** [Install Script](https://pixeldrain-bypass.gamedrive.org/pixeldrain-bypass.user.js)"
    )
    
    await message.reply(reply_text, disable_web_page_preview=True)

@app.on_message(filters.command(["status"]) & (filters.user(ADMINS) | filters.user(SUDOS)))
async def status_style_handler(client, message):
    uptime_seconds = int(time.time() - BOT_START_TIME)
    uptime_str = get_readable_time(uptime_seconds)
    mem = psutil.virtual_memory().percent
    cpu = psutil.cpu_percent()
    
    total, used, free = shutil.disk_usage(".")
    disk_free = free / (1024**3)
    
    active_count = 0
    queue_list = []
    
    for uid, tasks in ACTIVE_PROCESSES.items():
        for t_id, info in tasks.items():
            active_count += 1
            src = info.get("source_title", "Source")
            dst = info.get("dest_title_name", "Destination")
            queue_list.append(f"• {src} → {dst}")
    
    watcher_count = await db.db.watchers.count_documents({})
    queue_text = "\n".join(queue_list) if queue_list else "😴 No active downloads."
    
    auto_dl = "ON ✅" if await db.get_dl_status() else "OFF ❌"

    msg = (
        f"🔰 **SYSTEM DASHBOARD**\n\n"
        f"⏱ **Uptime:** `{uptime_str}`\n"
        f"🧠 **RAM:** `{mem}%`  │  ⚙️ **CPU:** `{cpu}%` \n"
        f"💿 **Disk Free:** `{disk_free:.1f} GB` \n"
        f"🔄 **Auto-Download:** `{auto_dl}` \n\n"
        f"👀 **Live Watchers:** `{watcher_count}` running\n"
        f"📉 **Active Downloads ({active_count})**\n"
        f"{queue_text}"
    )
    await message.reply(msg, quote=True)
    
@app.on_message(filters.command(["log"]) & (filters.user(ADMINS) | filters.user(SUDOS)))
async def send_log_handler(client: Client, message: Message):
    if os.path.exists("bot.log"):
        await message.reply_document("bot.log", caption="📄 **Bot Logs**\n(Updates automatically)")
    else:
        await message.reply("⚠️ Log file not found yet.")

@app.on_message(filters.command(["botstats"]) & filters.user(ADMINS))
async def bot_stats_handler(client: Client, message: Message):
    wait = await message.reply("📊 **Generating detailed stats...**")
    total_users = await db.total_users_count()
    all_users_cursor = await db.get_all_users()
    
    logged_in_list = []
    async for user in all_users_cursor:
        if user.get("session"):
            user_id = user['id']
            name = user.get("name") or f"User:{user_id}"
            user_tasks = ACTIVE_PROCESSES.get(user_id, {})
            
            if user_tasks:
                task_details = []
                for t_id, info in user_tasks.items():
                    src = info.get("source_title", "Source")
                    dst = info.get("dest_title", "Dest")
                    tot = info.get("total", 0)
                    curr = info.get("current", 0)
                    start_t = info.get("started", time.time())
                    
                    percent = (curr / tot * 100) if tot > 0 else 0
                    
                    elapsed = time.time() - start_t
                    eta_str = "Calculating..."
                    if curr > 0 and elapsed > 0:
                        eta_str = get_readable_time(int(((tot - curr) / (curr / elapsed))))

                    task_details.append(
                        f"      └ 🏃 {src} → {info.get('dest_title_name', 'Destination')}"
                    )
                    
                tasks_str = "\n" + "\n".join(task_details)
                logged_in_list.append(f"• **{name}** [`{user_id}`]{tasks_str}")
            else:
                logged_in_list.append(f"• **{name}** [`{user_id}`] (IDLE 😴)")

    logged_in_text = "\n\n".join(logged_in_list) if logged_in_list else "No users logged in."
    stats_msg = (
        "📊 **DETAILED BOT STATISTICS**\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
        f"👥 **Total Users:** `{total_users}`\n"
        f"🔑 **Logged-in Users:** `{len(logged_in_list)}`\n\n"
        f"📝 **User & Task Breakdown:**\n\n{logged_in_text}"
    )
    await wait.edit(stats_msg)
        
# ==============================================================================
# --- LOGIN / LOGOUT ---
# ==============================================================================

@app.on_message(filters.private & ~filters.forwarded & filters.command(["logout"]))
async def logout(client, message):
    user_id = message.from_user.id
    if not is_authorized(user_id):
        return await message.reply('⛔ <b>Access Denied:</b> You are not authorized to use this bot.', quote=True)
    if not await db.is_user_exist(user_id):
        return await message.reply_text("You are not logged in.")

    status_msg = await message.reply("📡 **Connecting to Telegram to terminate session...**")

    session_string = await db.get_session(user_id)
    api_id = await db.get_api_id(user_id)
    api_hash = await db.get_api_hash(user_id)

    if session_string:
        user_client = None
        try:
            use_api_id = int(api_id) if api_id else API_ID
            use_api_hash = api_hash if api_hash else API_HASH
            
            user_client = Client(
                f"logout_{user_id}_{int(time.time())}", 
                session_string=session_string, 
                api_id=use_api_id, 
                api_hash=use_api_hash,
                no_updates=True,
                in_memory=True
            )
            
            await user_client.connect()
            try:
                await user_client.log_out()
                await status_msg.edit("✅ **Session successfully removed from Telegram Devices.**")
            except Exception as e:
                if "terminated" in str(e) or "Connection" in str(e):
                    await status_msg.edit("✅ **Session terminated successfully.**")
                else:
                    raise e
            
        except AuthKeyUnregistered:
            await status_msg.edit("⚠️ **Session was already invalid.** Cleaning local database...")
        except Exception as e:
            print(f"Remote logout warning: {e}")
            await status_msg.edit("✅ **Local session cleared.** (Remote session might already be gone)")
        finally:
            try:
                if user_client and user_client.is_connected:
                    await user_client.disconnect()
            except Exception: pass

    if user_id in USER_CLIENTS:
        try:
            await USER_CLIENTS[user_id].stop()
        except Exception:
            pass
        del USER_CLIENTS[user_id]

    await db.set_session(user_id, session=None)
    await db.set_api_id(user_id, api_id=None)
    await db.set_api_hash(user_id, api_hash=None)
    
    await message.reply("**Logout Complete** ♦\n(You are now disconnected)")

@app.on_message(filters.private & ~filters.forwarded & filters.command(["login"]))
async def login_handler(bot: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if not is_authorized(user_id):
        return await message.reply('⛔ <b>Access Denied:</b> You are not authorized to use this bot.', quote=True)
    
    if not await db.is_user_exist(message.from_user.id):
        await db.add_user(message.from_user.id, message.from_user.first_name)
        
    user_data = await db.get_session(message.from_user.id)
    if user_data is not None:
        await message.reply("**You Are Already Logged In. First /logout Your Old Session. Then Do Login.**")
        return  
    user_id = int(message.from_user.id)

    if API_ID != 0 and API_HASH:
        await message.reply("**🔑 Specific API ID and HASH found in variables. Using them automatically...**")
        api_id = API_ID
        api_hash = API_HASH
    else:
        api_id_msg = await bot.ask(user_id, "<b>Send Your API ID.</b>", filters=filters.text)
        if api_id_msg.text == '/cancel':
            return await api_id_msg.reply('<b>process cancelled !</b>')
        try:
            api_id = int(api_id_msg.text)
            if api_id < 1000000 or api_id > 99999999:
                 await api_id_msg.reply("**❌ Invalid API ID**\n\nPlease start again with /login.", quote=True)
                 return
        except ValueError:
            await api_id_msg.reply("**Api id must be an integer, start your process again by /login**", quote=True)
            return
        
        api_hash_msg = await bot.ask(user_id, "**Now Send Me Your API HASH**", filters=filters.text)
        if api_hash_msg.text == '/cancel':
            return await api_hash_msg.reply('<b>process cancelled !</b>')
        api_hash = api_hash_msg.text

        if len(api_hash) != 32:
             await api_hash_msg.reply("**❌ Invalid API HASH**\n\nPlease start again with /login.", quote=True)
             return

    login_text = (
        "🔐 **Login Process Initiated**\n\n"
        "Please send your **Phone Number** in international format.\n"
        "Example: `+1234567890`\n\n"
        "🛡️ *Your session is stored securely locally.*"
    )

    phone_number_msg = await bot.ask(chat_id=user_id, text=login_text, filters=filters.text)
    if phone_number_msg.text=='/cancel':
        return await phone_number_msg.reply('<b>process cancelled !</b>')
    phone_number = phone_number_msg.text
    
    client_auth = Client(f"login_{user_id}_{int(time.time())}", api_id=api_id, api_hash=api_hash, in_memory=True)
    await client_auth.connect()
    
    await phone_number_msg.reply("Sending OTP...")
    try:
        code = await client_auth.send_code(phone_number)
        phone_code_msg = await bot.ask(user_id, "Please check for an OTP in official telegram account. If you got it, send OTP here after reading the below format. \n\nIf OTP is `12345`, **please send it as** `1 2 3 4 5`.\n\n**Enter /cancel to cancel The Procces**", filters=filters.text, timeout=600)
    except PhoneNumberInvalid:
        await phone_number_msg.reply('`PHONE_NUMBER` **is invalid.**')
        await client_auth.disconnect()
        return
        
    if phone_code_msg.text=='/cancel':
        await client_auth.disconnect()
        return await phone_code_msg.reply('<b>process cancelled !</b>')
        
    try:
        phone_code = phone_code_msg.text.replace(" ", "")
        await client_auth.sign_in(phone_number, code.phone_code_hash, phone_code)
    except PhoneCodeInvalid:
        await phone_code_msg.reply('**OTP is invalid.**')
        await client_auth.disconnect()
        return
    except PhoneCodeExpired:
        await phone_code_msg.reply('**OTP is expired.**')
        await client_auth.disconnect()
        return
    except SessionPasswordNeeded:
        two_step_msg = await bot.ask(user_id, '**Your account has enabled two-step verification. Please provide the password.\n\nEnter /cancel to cancel The Procces**', filters=filters.text, timeout=300)
        if two_step_msg.text=='/cancel':
            await client_auth.disconnect()
            return await two_step_msg.reply('<b>process cancelled !</b>')
        try:
            password = two_step_msg.text
            await client_auth.check_password(password=password)
        except PasswordHashInvalid:
            await two_step_msg.reply('**Invalid Password Provided**')
            await client_auth.disconnect()
            return
            
    string_session = await client_auth.export_session_string()
    await client_auth.disconnect()
    
    if len(string_session) < SESSION_STRING_SIZE:
        return await message.reply('<b>invalid session sring</b>')
    try:
        user_data = await db.get_session(message.from_user.id)
        if user_data is None:
            uclient = Client(f"verify_{message.from_user.id}_{int(time.time())}", session_string=string_session, api_id=api_id, api_hash=api_hash, in_memory=True)
            await uclient.connect()
            
            await db.set_session(message.from_user.id, session=string_session)
            await db.set_api_id(message.from_user.id, api_id=api_id)
            await db.set_api_hash(message.from_user.id, api_hash=api_hash)
            
            try:
                await uclient.disconnect()
            except Exception:
                pass
    except Exception as e:
        return await message.reply_text(f"<b>ERROR IN LOGIN:</b> `{e}`")
    await bot.send_message(message.from_user.id, "<b>Account Login Successfully.\n\nIf You Get Any Error Related To AUTH KEY Then /logout first and /login again</b>")

# ==============================================================================
# --- BROADCAST ---
# ==============================================================================

async def broadcast_messages(user_id, message):
    start_time = time.time()
    try:
        await message.copy(chat_id=user_id)
        elapsed = time.time() - start_time
        await asyncio.sleep(max(0, 1.5 - elapsed)) 
        return True, "Success"
    except FloodWait as e:
        if e.value > 60:
            return False, "Error"
        await asyncio.sleep(e.value)
        return await broadcast_messages(user_id, message)
    except InputUserDeactivated:
        await db.delete_user(int(user_id))
        return False, "Deleted"
    except UserIsBlocked:
        await db.delete_user(int(user_id))
        return False, "Blocked"
    except PeerIdInvalid:
        await db.delete_user(int(user_id))
        return False, "Error"
    except Exception as e:
        return False, "Error"

@app.on_message(filters.command("broadcast") & filters.user(ADMINS) & filters.reply)
async def broadcast(bot, message):
    users = await db.get_all_users()
    b_msg = message.reply_to_message
    if not b_msg:
        return await message.reply_text("**Reply This Command To Your Broadcast Message**")
    sts = await message.reply_text(text='Broadcasting your messages...')
    start_time = time.time()
    total_users = await db.total_users_count()
    done = 0
    blocked = 0
    deleted = 0
    failed = 0
    success = 0
    async for user in users:
        if 'id' in user:
            pti, sh = await broadcast_messages(int(user['id']), b_msg)
            if pti:
                success += 1
            elif pti == False:
                if sh == "Blocked":
                    blocked += 1
                elif sh == "Deleted":
                    deleted += 1
                elif sh == "Error":
                    failed += 1
            done += 1
            if not done % 20:
                await sts.edit(f"Broadcast in progress:\n\nTotal Users {total_users}\nCompleted: {done} / {total_users}\nSuccess: {success}\nBlocked: {blocked}\nDeleted: {deleted}")
        else:
            done += 1
            failed += 1
            if not done % 20:
                await sts.edit(f"Broadcast in progress:\n\nTotal Users {total_users}\nCompleted: {done} / {total_users}\nSuccess: {success}\nBlocked: {blocked}\nDeleted: {deleted}")

    time_taken = str(datetime.timedelta(seconds=int(time.time()-start_time)))
    await sts.edit(f"Broadcast Completed:\nCompleted in {time_taken} seconds.\n\nTotal Users {total_users}\nCompleted: {done} / {total_users}\nSuccess: {success}\nBlocked: {blocked}\nDeleted: {deleted}")

# ==============================================================================
# --- WATCHER SETUP WIZARD ---
# ==============================================================================

@app.on_message(filters.command(["watch"]) & filters.private)
async def watch_setup(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_authorized(user_id):
        return await message.reply('⛔ <b>Access Denied:</b> You are not authorized to use this bot.', quote=True)
    
    if len(message.command) < 2:
        return await message.reply("**Usage:**\n`/watch https://t.me/channel/123`\n(Supports Topics too!)")
    
    link_text = message.command[1]
    
    wait_msg = await message.reply("🔎 **Analyzing Source...**", quote=True)
    is_restricted, status_text = await check_link_restriction(user_id, link_text)
    
    source_thread_id = None
    if "t.me/c/" in link_text:
        parts = link_text.split("t.me/c/")[1].split("/")
        if len(parts) >= 2 and parts[1].isdigit():
            source_thread_id = int(parts[1])
    elif "t.me/" in link_text:
        parts = link_text.split("t.me/")[1].split("/")
        if len(parts) >= 2 and parts[1].isdigit():
            source_thread_id = int(parts[1])
    
    await wait_msg.delete()
    
    PENDING_TASKS[user_id] = {
        "mode": "WATCHER", 
        "link": link_text,
        "source_thread_id": source_thread_id,
        "is_restricted": is_restricted,
        "status": "waiting_choice"
    }
    
    buttons = [
        [InlineKeyboardButton("📂 Send to DM (Here)", callback_data="dest_dm")],
        [InlineKeyboardButton("📢 Send to Channel/Group", callback_data="dest_custom")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_setup")]
    ]
    
    await message.reply(
        f"👀 **Watcher Setup**\n\n"
        f"{status_text}\n"
        f"{(f'🔹 **Source Topic:** `{source_thread_id}` detected!' if source_thread_id else '')}\n\n"
        "**Where should new messages go?**",
        reply_markup=InlineKeyboardMarkup(buttons),
        quote=True
    )

@app.on_message(filters.command(["removetarget", "remove_target"]) & filters.private)
async def remove_target_cmd(client, message):
    if len(message.command) < 3:
        return await message.reply("**Usage:** `/removetarget [source_id] [dest_id]`")
    src = message.command[1]
    dst = message.command[2]
    
    if await db.remove_watcher_target(src, dst):
        await message.reply("✅ **Target Removed Successfully!**\nNo more messages will route here from that source.")
    else:
        await message.reply("⚠️ Target or Source not found.")

@app.on_message(filters.command(["removesource", "unwatch"]) & filters.private)
async def remove_source_cmd(client, message):
    if len(message.command) < 2:
        return await message.reply("**Usage:** `/unwatch [source_id]`")
    
    try:
        src = int(message.command[1])
    except ValueError:
        return await message.reply("⚠️ **Invalid format!**\nPlease pass the numerical **Source ID** (e.g. `-100123...`). You can find this ID by sending `/watchers`.")
        
    if await db.remove_watcher(src):
        await message.reply("✅ **Source Route Removed Successfully!**")
    else:
        await message.reply("⚠️ Source not found.")

@app.on_message(filters.command(["watchers", "list"]) & filters.private)
async def list_watchers(client, message):
    user_id = message.from_user.id
    cursor = await db.get_all_watchers()
    watchers = await cursor.to_list(length=100)
    
    user_watchers = [w for w in watchers if w.get('user_id') == user_id]
    
    if not user_watchers:
        return await message.reply("💤 **No active watchers found.**")
    
    text = "📊 **Your Multi-Source Channel Mappings:**\n\n"
    
    for idx, w in enumerate(user_watchers, 1):
        src_id = w['source_id']
        src_display = w.get('source_title') or str(src_id)
        if w.get('source_thread'): src_display += f" (Topic: {w['source_thread']})"
        
        targets = w.get('targets', [])
        if not targets and 'dest_id' in w: 
            targets = [{"dest_id": w['dest_id'], "dest_thread": w.get('dest_thread'), "dest_title": w.get('dest_title')}]
            
        text += f"**{idx}. 📥 {src_display}**\n"
        text += f"   <code>{src_id}</code>\n"
        text += f"   🎛 **Filters:** `{', '.join(w.get('allowed_types', ['Legacy']))}`\n"
        
        fmts = w.get('text_format', [])
        fmt_str = ", ".join(fmts) if fmts else "Default"
        text += f"   📝 **Format:** `{fmt_str}`\n"
        text += f"   ⏱ **Delay:** `{w.get('delay', 0)}s`\n"
        
        text += f"   ⤵️ **Targets ({len(targets)}):**\n"
        
        for t in targets:
            dst_id = t['dest_id']
            dst_display = t.get('dest_title') or str(dst_id)
            if t.get('dest_thread'): dst_display += f" (Topic: {t['dest_thread']})"
            text += f"   • {dst_display} (<code>{dst_id}</code>)\n"
        text += "\n"
        
    await message.reply(text)
    
@app.on_callback_query(filters.regex("^unwatch_"))
async def unwatch_callback(client, query):
    if query.data == "unwatch_all":
        user_id = query.from_user.id
        result = await db.db.watchers.delete_many({'user_id': user_id})
        await query.message.edit(f"✅ **Success!**\n\n🗑 Removed `{result.deleted_count}` active watchers.")
        return

    data = query.data.split("_")
    source_id = int(data[1])
    topic_id = int(data[2])
    
    query_db = {'source_id': source_id}
    if topic_id != 0:
        query_db['source_thread'] = topic_id
        
    watcher = await db.db.watchers.find_one(query_db)
    
    src_name = str(source_id)
    dest_name = "Unknown"
    
    if watcher:
        src_name = watcher.get('source_title') or str(source_id)
        dest_name = watcher.get('dest_title') or str(watcher.get('dest_id'))

    if topic_id == 0:
        await db.db.watchers.delete_many({'source_id': source_id, 'source_thread': None})
        await db.db.watchers.delete_many({'source_id': source_id, 'source_thread': {'$exists': False}})
    else:
        await db.db.watchers.delete_many({'source_id': source_id, 'source_thread': topic_id})
        
    await query.message.edit(
        f"🗑 **Active Watcher Task Removed**\n\n"
        f"From: **{src_name}**\n"
        f"To: **{dest_name}**"
    )
    
# ==============================================================================
# --- CORE: receive links / start tasks / processing / cancel checks ---
# ==============================================================================

@app.on_message((filters.text | filters.caption) & filters.private & ~filters.command(["dl", "start", "help", "cancel", "botstats", "login", "logout", "broadcast", "status", "watch", "unwatch", "watchers", "removetarget", "removesource", "log", "tasks", "progress", "toggledl"]))
async def save(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_authorized(user_id):
        return await message.reply('⛔ <b>Access Denied:</b> You are not authorized to use this bot.', quote=True)
    if user_id in PENDING_TASKS:
        if PENDING_TASKS[user_id].get("status") == "waiting_id":
            await process_custom_destination(client, message)
            return
        if PENDING_TASKS[user_id].get("status") == "waiting_speed":
            await process_speed_input(client, message)
            return

    link_text = message.text or message.caption
    if not link_text or "https://t.me/" not in link_text:
        return

    wait_msg = await message.reply("🔎 **Analyzing Link...**", quote=True)
    is_restricted, status_text = await check_link_restriction(user_id, link_text)
    await wait_msg.delete()

    PENDING_TASKS[user_id] = {
        "link": link_text, 
        "status": "waiting_choice",
        "is_restricted": is_restricted 
    }
    
    buttons = [
        [InlineKeyboardButton("📂 Send to DM (Here)", callback_data="dest_dm")],
        [InlineKeyboardButton("📢 Send to Channel/Group", callback_data="dest_custom")],
        [InlineKeyboardButton("❌ Cancel Setup", callback_data="cancel_setup")]
    ]
    
    await message.reply(
        f"✨ **Link Detected!**\n\n"
        f"{status_text}\n\n"
        "Where should I send the files?",
        reply_markup=InlineKeyboardMarkup(buttons),
        quote=True
    )
    
@app.on_message(filters.command(["dl"]) & (filters.private | filters.group))
async def dl_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_authorized(user_id):
        return await message.reply('⛔ <b>Access Denied:</b> You are not authorized to use this bot.', quote=True)
    link_text = ""
    
    reply = message.reply_to_message
    if reply and (reply.text or reply.caption):
        link_text = reply.text or reply.caption
    elif len(message.command) > 1:
        link_text = message.text.split(None, 1)[1]
        
    if not link_text or "https://t.me/" not in link_text:
        await message.reply_text(
            "**Usage:**\n"
            "• Reply to a link with `/dl`\n"
            "• Or send `/dl https://t.me/...`"
        )
        return

    wait_msg = await message.reply("🔎 **Analyzing Link...**", quote=True)
    is_restricted, status_text = await check_link_restriction(user_id, link_text)
    await wait_msg.delete()

    if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        PENDING_TASKS[user_id] = {
            "link": link_text,
            "dest_chat_id": message.chat.id,
            "dest_thread_id": message.message_thread_id,
            "dest_title": message.chat.title or "This Group",
            "status": "waiting_speed",
            "is_restricted": is_restricted 
        }
        await message.reply(f"✨ **Link Analyzed!**\n{status_text}", quote=True)
        await ask_for_speed(message)
        return

    PENDING_TASKS[user_id] = {
        "link": link_text, 
        "status": "waiting_choice",
        "is_restricted": is_restricted
    }
    
    buttons = [
        [InlineKeyboardButton("📂 Send to DM (Here)", callback_data="dest_dm")],
        [InlineKeyboardButton("📢 Send to Channel/Group", callback_data="dest_custom")],
        [InlineKeyboardButton("❌ Cancel Setup", callback_data="cancel_setup")]  
    ]
    
    await message.reply(
        f"✨ **Link Detected!**\n\n"
        f"{status_text}\n\n"
        "I am ready to process this content. Please tell me where you want the files sent:",
        reply_markup=InlineKeyboardMarkup(buttons),
        quote=True
    )
    
@app.on_callback_query(filters.regex("^dest_"))
async def destination_callback(client: Client, query):
    user_id = query.from_user.id
    if user_id not in PENDING_TASKS:
        return await query.answer("❌ Task expired. Send link again.", show_alert=True)
    choice = query.data
    if choice == "dest_dm":
        PENDING_TASKS[user_id]["dest_chat_id"] = user_id
        PENDING_TASKS[user_id]["dest_thread_id"] = None
        await ask_for_speed(query.message)
    elif choice == "dest_custom":
        PENDING_TASKS[user_id]["status"] = "waiting_id"
        buttons = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_setup")]]
        await query.message.edit(
            "📝 **Send the Target Chat ID**\n\n"
            "Examples:\n"
            "• Channel/Group: `-100123456789`\n"
            "• Specific Topic: `-100123456789/5`\n\n"
            "⚠️ __Make sure I am an admin in that chat!__",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

# ======================================
# --- NAYA FORMAT MENU LOGIC START ---
# ======================================
def get_format_keyboard(current_formats):
    buttons = [
        [
            InlineKeyboardButton(f"{'✅' if 'Bold' in current_formats else '❌'} Bold", callback_data="format_toggle:Bold"),
            InlineKeyboardButton(f"{'✅' if 'Italic' in current_formats else '❌'} Italic", callback_data="format_toggle:Italic")
        ],
        [InlineKeyboardButton("✅ Default (Keep Original)", callback_data="format_none")],
        [InlineKeyboardButton("⏭ Next: Select Filters", callback_data="format_start")],
        [InlineKeyboardButton("🛑 Cancel Setup", callback_data="cancel_setup")]
    ]
    return InlineKeyboardMarkup(buttons)

async def show_format_menu(message_or_query, user_id):
    task_data = PENDING_TASKS.get(user_id)
    if not task_data: return
    
    if "text_format" not in task_data:
        task_data["text_format"] = [] 
    task_data["status"] = "waiting_format"
    
    kb = get_format_keyboard(task_data["text_format"])
    text = "📝 **Caption Formatting**\n\nChoose how your caption should look:\n*(Select multiple if you want, or leave Default)*"
    
    if isinstance(message_or_query, CallbackQuery):
        await message_or_query.message.edit_text(text, reply_markup=kb)
    else:
        await message_or_query.reply(text, reply_markup=kb, quote=True)

@app.on_callback_query(filters.regex("^format_toggle:(.+)"))
async def format_toggle_cb(client, query):
    user_id = query.from_user.id
    if user_id not in PENDING_TASKS: return await query.answer("Expired.", show_alert=True)
    fmt = query.data.split(":")[1]
    current = PENDING_TASKS[user_id].get("text_format", [])
    if fmt in current: current.remove(fmt)
    else: current.append(fmt)
    PENDING_TASKS[user_id]["text_format"] = current
    try: await query.message.edit_reply_markup(get_format_keyboard(current))
    except Exception: pass
    await query.answer()

@app.on_callback_query(filters.regex("^format_none$"))
async def format_none_cb(client, query):
    user_id = query.from_user.id
    if user_id not in PENDING_TASKS: return await query.answer("Expired.", show_alert=True)
    PENDING_TASKS[user_id]["text_format"] = []
    try: await query.message.edit_reply_markup(get_format_keyboard([]))
    except Exception: pass
    await query.answer()

@app.on_callback_query(filters.regex("^format_start$"))
async def format_start_cb(client, query):
    user_id = query.from_user.id
    if user_id not in PENDING_TASKS: return await query.answer("Expired.", show_alert=True)
    await show_filter_menu(query, user_id)
# ======================================
# --- NAYA FORMAT MENU LOGIC END ---
# ======================================

def get_filter_keyboard(current_types):
    buttons = []
    row = []
    for t in ALL_MSG_TYPES:
        icon = "✅" if t in current_types else "❌"
        row.append(InlineKeyboardButton(f"{icon} {t}", callback_data=f"filter_toggle:{t}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row: buttons.append(row)
    buttons.append([
        InlineKeyboardButton("✅ Select All", callback_data="filter_all"), 
        InlineKeyboardButton("❌ Clear All", callback_data="filter_none")
    ])
    buttons.append([InlineKeyboardButton("🚀 Proceed / Save Setup", callback_data="filter_start")])
    buttons.append([InlineKeyboardButton("🛑 Cancel Setup", callback_data="cancel_setup")])
    return InlineKeyboardMarkup(buttons)

async def show_filter_menu(message_or_query, user_id):
    task_data = PENDING_TASKS.get(user_id)
    if not task_data: return
    
    if "allowed_types" not in task_data:
        task_data["allowed_types"] = ["Video", "Document"] 
    task_data["status"] = "waiting_filter"
    
    kb = get_filter_keyboard(task_data["allowed_types"])
    text = "🎛 **Content Filter**\n\nSelect the media types you want to forward or download.\n*(Default: Strictly Videos & Documents)*"
    
    if isinstance(message_or_query, CallbackQuery):
        await message_or_query.message.edit_text(text, reply_markup=kb)
    else:
        await message_or_query.reply(text, reply_markup=kb, quote=True)

@app.on_callback_query(filters.regex("^filter_toggle:(.+)"))
async def filter_toggle_cb(client, query):
    user_id = query.from_user.id
    if user_id not in PENDING_TASKS: return await query.answer("Expired.", show_alert=True)
    mtype = query.data.split(":")[1]
    allowed = PENDING_TASKS[user_id].get("allowed_types", ["Video", "Document"])
    if mtype in allowed: allowed.remove(mtype)
    else: allowed.append(mtype)
    PENDING_TASKS[user_id]["allowed_types"] = allowed
    try: await query.message.edit_reply_markup(get_filter_keyboard(allowed))
    except Exception: pass
    await query.answer()

@app.on_callback_query(filters.regex("^filter_all$"))
async def filter_all_cb(client, query):
    user_id = query.from_user.id
    if user_id not in PENDING_TASKS: return await query.answer("Expired.", show_alert=True)
    PENDING_TASKS[user_id]["allowed_types"] = ALL_MSG_TYPES.copy()
    try: await query.message.edit_reply_markup(get_filter_keyboard(ALL_MSG_TYPES))
    except Exception: pass
    await query.answer()

@app.on_callback_query(filters.regex("^filter_none$"))
async def filter_none_cb(client, query):
    user_id = query.from_user.id
    if user_id not in PENDING_TASKS: return await query.answer("Expired.", show_alert=True)
    PENDING_TASKS[user_id]["allowed_types"] = []
    try: await query.message.edit_reply_markup(get_filter_keyboard([]))
    except Exception: pass
    await query.answer()

@app.on_callback_query(filters.regex("^filter_start$"))
async def filter_start_cb(client, query):
    user_id = query.from_user.id
    if user_id not in PENDING_TASKS: return await query.answer("Expired.", show_alert=True)
    
    task_data = PENDING_TASKS.pop(user_id)
    allowed_types = task_data.get("allowed_types", ["Video", "Document"])
    
    if not allowed_types: 
        PENDING_TASKS[user_id] = task_data 
        return await query.answer("❌ Select at least one type!", show_alert=True)
        
    await query.answer("🚀 Proceeding...", show_alert=False)
    try: await query.message.edit_text("⏳ **Initializing Task... Please Wait!**")
    except Exception: pass
    
    delay = task_data.get("delay", 3)
    if task_data.get("mode") == "WATCHER":
        await finalize_watcher_setup(client, query.message, task_data, delay, user_id=user_id)
    else:
        await start_task_final(client, query.message, task_data, delay, user_id=user_id)

async def process_custom_destination(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    try:
        if message.reply_to_message and message.reply_to_message.from_user.is_self:
            await message.reply_to_message.delete()
    except Exception:
        pass
    try:
        dest_chat_id = None
        dest_thread_id = None
        
        if "t.me/c/" in text:
            parts = text.split("t.me/c/")[1].split("/")
            chat_part = parts[0].strip()
            dest_chat_id = int(f"-100{chat_part}") if not chat_part.startswith("-100") else int(chat_part)
            if len(parts) > 1 and parts[1].strip().isdigit():
                dest_thread_id = int(parts[1].strip())
        elif "t.me/" in text:
            parts = text.split("t.me/")[1].split("/")
            dest_chat_id = parts[0].strip()
            if len(parts) > 1 and parts[1].strip().isdigit():
                dest_thread_id = int(parts[1].strip())
        elif "/" in text:
            parts = text.split("/")
            chat_part = parts[0].strip()
            clean_part = chat_part.lstrip('-')
            if clean_part.isdigit():
                raw_num = int(chat_part)
                if raw_num > 0 and raw_num > 10000000:
                    dest_chat_id = int(f"-100{raw_num}")
                elif not str(raw_num).startswith("-100"):
                    dest_chat_id = int(f"-100{abs(raw_num)}")
                else:
                    dest_chat_id = raw_num
            else:
                dest_chat_id = chat_part
            if len(parts) > 1 and parts[1].strip().isdigit():
                dest_thread_id = int(parts[1].strip())
        else:
            clean_num = text.lstrip('-')
            if clean_num.isdigit():
                raw_num = int(text)
                if raw_num > 0 and raw_num > 10000000:
                    dest_chat_id = int(f"-100{raw_num}")
                elif not str(raw_num).startswith("-100"):
                    dest_chat_id = int(f"-100{abs(raw_num)}")
                else:
                    dest_chat_id = raw_num
            else:
                dest_chat_id = text
            
        try:
            try:
                chat = await client.get_chat(dest_chat_id)
            except Exception as e_bot:
                if user_id in USER_CLIENTS and USER_CLIENTS[user_id].is_connected:
                    chat = await USER_CLIENTS[user_id].get_chat(dest_chat_id)
                else:
                    raise e_bot
            title = chat.title or "Target Chat"
            dest_chat_id = chat.id 
            
            if not getattr(chat, "is_forum", False):
                dest_thread_id = None
                
        except Exception as e:
            await message.reply(f"❌ **Invalid Chat ID/Link** or I am not an admin there.\nError: `{e}`")
            return
            
        wait_msg = await message.reply("🔄 **Testing destination access...**", quote=True)
        success, error_msg = await test_destination_access(client, dest_chat_id, dest_thread_id)
        
        if not success:
            await wait_msg.edit(f"❌ **Setup Failed! Cannot write to destination.**\n\nPlease ensure I am an admin in `{title}` with posting permissions.\n**Error:** `{error_msg}`")
            return
            
        await wait_msg.delete()
            
        PENDING_TASKS[user_id]["dest_chat_id"] = dest_chat_id
        PENDING_TASKS[user_id]["dest_thread_id"] = dest_thread_id
        PENDING_TASKS[user_id]["dest_title"] = title
        PENDING_TASKS[user_id]["status"] = "waiting_speed"
        await ask_for_speed(message)
    except Exception as e:
        await message.reply(f"❌ Invalid format. Please send a valid link or ID.\nError: `{e}`")

async def ask_for_speed(message: Message):
    buttons = [
        [InlineKeyboardButton("⚡ Default (3s)", callback_data="speed_default")],
        [InlineKeyboardButton("⚙️ Manual Speed", callback_data="speed_manual")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_setup")]
    ]
    text = "**🚀 Select Forwarding Speed**\n\nHow fast should I process messages?"
    if isinstance(message, Message) and message.from_user.is_bot:
        await message.edit(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message.reply(text, reply_markup=InlineKeyboardMarkup(buttons), quote=True)

@app.on_callback_query(filters.regex("^speed_"))
async def speed_callback(client: Client, query):
    user_id = query.from_user.id
    if user_id not in PENDING_TASKS:
        await query.answer("❌ Task expired.", show_alert=True)
        return
    
    choice = query.data
    task_data = PENDING_TASKS[user_id]
    
    if choice == "speed_manual":
        PENDING_TASKS[user_id]["status"] = "waiting_speed"
        await query.message.edit(
            "⏱ **Enter Delay (Seconds)**\n\n"
            "Every time a new message arrives, I will wait this long before forwarding it.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_setup")]])
        )
        return

    if choice == "speed_default":
        PENDING_TASKS[user_id]["delay"] = 0 if task_data.get("mode") == "WATCHER" else 3
        await show_format_menu(query, user_id)

async def process_speed_input(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    if not text.isdigit(): return await message.reply("❌ Numbers only.")
    
    delay = int(text)
    if user_id in PENDING_TASKS:
        PENDING_TASKS[user_id]["delay"] = delay
        await show_format_menu(message, user_id)

async def finalize_watcher_setup(client, message, data, delay, user_id=None):
    if user_id is None:
        user_id = message.from_user.id if message.from_user else None
    if not user_id:
        return 
    src_link = data['link']
    source_id = None
    source_title = "Unknown Source"
    
    user_session = await db.get_session(user_id)
    api_id = await db.get_api_id(user_id)
    api_hash = await db.get_api_hash(user_id)
    
    if not user_session:
        return await message.reply("❌ **You are not logged in.**\n\nWatcher mode requires your account to 'listen' for new messages.\nPlease /login first.")

    if user_id not in USER_CLIENTS:
        status_msg = await message.reply("🔄 **Starting your Listener Client...**")
        try:
            u_api = api_id or API_ID
            u_hash = api_hash or API_HASH
            
            new_client = Client(
                f"watcher_setup_{user_id}", 
                session_string=user_session, 
                api_id=u_api, 
                api_hash=u_hash, 
                workers=4, 
                ipv6=False,
                in_memory=True 
            )
            new_client.add_handler(MessageHandler(user_watcher_handler))
            
            await new_client.start()
            USER_CLIENTS[user_id] = new_client
            await status_msg.delete()
            
        except Exception as e:
            return await status_msg.edit(f"❌ **Session Error:** `{e}`\n\nTry /logout and /login again.")

    user_client = USER_CLIENTS[user_id]
    try:
        join_target = None
        if "t.me/c/" in src_link:
            chat_str = src_link.split("t.me/c/")[1].split("/")[0]
            source_id = int("-100" + chat_str)
            join_target = source_id
            try:
                chat = await user_client.get_chat(source_id)
                source_title = chat.title or str(source_id)
            except Exception:
                source_title = str(source_id)
        else:
            username = src_link.replace("https://", "").replace("http://", "").replace("t.me/", "").split("/")[0]
            chat = await user_client.get_chat(username)
            source_id = chat.id
            source_title = chat.title or str(source_id)
            join_target = username 
            
        try: 
            await user_client.join_chat(join_target)
        except UserAlreadyParticipant:
            pass 
        except Exception as e:
            print(f"⚠️ Auto-join warning for {join_target}: {e}")
            
        try:
            await user_client.get_chat_member(source_id, "me")
        except Exception:
            return await message.reply(f"❌ **Watcher Setup Blocked!**\n\nThe account you logged in with (`/login`) is **NOT** a member of `{source_title}`. Telegram blocks live updates to non-members.\n\n**Fix:** Please manually open Telegram on the account you logged into the bot with, and join the source channel first!")
        
    except Exception as e:
        return await message.reply(f"❌ **Could not access Source.**\nMake sure your User Account is a member of that channel.\nError: `{e}`")

    dest_chat_id = data.get('dest_chat_id')
    
    if dest_chat_id and dest_chat_id != user_id: 
        success, error_msg = await test_destination_access(app, dest_chat_id, data.get('dest_thread_id'))
        if not success:
            return await message.reply(f"❌ **Watcher Setup Failed!**\nCannot write to the target destination.\n**Error:** `{error_msg}`")

    await db.add_watcher(
        user_id=user_id,
        source_id=source_id,
        dest_id=dest_chat_id,
        source_thread=data.get('source_thread_id'),
        dest_thread=data.get('dest_thread_id'),
        delay=delay,
        is_restricted=data['is_restricted'],
        source_title=source_title,
        dest_title=data.get('dest_title', str(data.get('dest_chat_id'))),
        allowed_types=data.get('allowed_types'),
        text_format=data.get('text_format')
    )
    
    fmt_str = ", ".join(data.get('text_format', [])) if data.get('text_format') else "Default"

    await message.reply(
        f"✅ **Watcher/Routing Active!**\n\n"
        f"👀 Source: `{source_title}`\n"
        f"📂 Source Topic: `{data.get('source_thread_id', 'All')}`\n"
        f"🎯 Destination: `{data.get('dest_title')}`\n"
        f"📂 Dest Topic: `{data.get('dest_thread_id', 'None')}`\n"
        f"⏱ Delay: `{delay}s`\n"
        f"🔒 Restricted Mode: `{'Yes' if data['is_restricted'] else 'No'}`\n"
        f"📝 Format: `{fmt_str}`\n"
        f"🎛 Filter: `{', '.join(data.get('allowed_types', []))}`"
    )

def parse_log_channel(log_channel_str):
    if not log_channel_str:
        return None, None
    log_channel_str = str(log_channel_str).strip()
    topic_id = None
    
    if "t.me/c/" in log_channel_str:
        parts = log_channel_str.split("t.me/c/")[1].split("/")
        chat_part = parts[0].strip()
        if len(parts) > 1 and parts[1].strip().isdigit():
            topic_id = int(parts[1].strip())
        chat_id = int(f"-100{chat_part}") if not chat_part.startswith("-100") else int(chat_part)
        return chat_id, topic_id
    elif "t.me/" in log_channel_str:
        parts = log_channel_str.split("t.me/")[1].split("/")
        chat_part = parts[0].strip()
        if len(parts) > 1 and parts[1].strip().isdigit():
            topic_id = int(parts[1].strip())
        return chat_part, topic_id

    if "/" in log_channel_str:
        parts = log_channel_str.split("/")
        chat_part = parts[0].strip()
        if len(parts) > 1 and parts[1].strip().isdigit():
            topic_id = int(parts[1].strip())
    else:
        chat_part = log_channel_str.strip()

    clean_num = chat_part.lstrip("-")
    if clean_num.isdigit():
        num = int(chat_part)
        if num > 0:
            chat_id = int(f"-100{num}") if num > 10000000 else num
        elif not str(num).startswith("-100"):
            chat_id = int(f"-100{abs(num)}")
        else:
            chat_id = num
        return chat_id, topic_id
    else:
        return chat_part, topic_id

async def send_log(text: str):
    print(f"[BOT LOG] {text}")
    if not LOG_CHANNEL:
        return
    try:
        chat_id, topic_id = parse_log_channel(LOG_CHANNEL)
        if not chat_id:
            return
        await app.send_message(chat_id, text, reply_to_message_id=topic_id)
    except Exception as e:
        print(f"❌ Failed to send log to Telegram: {e} | LOG_CHANNEL: '{LOG_CHANNEL}'. Please ensure Bot is an ADMIN in LOG_CHANNEL and ID has -100 prefix.")

async def check_disk_space(required_bytes=500 * 1024 * 1024):
    try:
        total, used, free = shutil.disk_usage(".")
        if free < required_bytes: 
            return False
        return True
    except Exception:
        return True

async def cleanup_watchdog():
    while True:
        await asyncio.sleep(600) 
        try:
            download_path = Path("./downloads")
            if not download_path.exists(): continue
            
            current_time = time.time()
            max_age = 2 * 60 * 60 
            
            for user_folder in download_path.iterdir():
                if user_folder.is_dir():
                    for task_folder in user_folder.iterdir():
                        if task_folder.is_dir():
                            folder_time = task_folder.stat().st_mtime
                            if (current_time - folder_time) > max_age:
                                await asyncio.to_thread(shutil.rmtree, task_folder, ignore_errors=True)
                                await send_log(f"🧹 **Auto-Cleanup:** Deleted stuck folder `{task_folder.name}` (Older than 2h)")
        except Exception as e:
            print(f"Watchdog Error: {e}")
            
async def start_task_final(client: Client, message_context: Message, task_data: dict, delay: int, user_id: int):
    if not await check_disk_space():
        msg = "⚠️ **Server Busy:** Disk is almost full. Please wait for other tasks to finish."
        if isinstance(message_context, Message):
             await message_context.reply(msg, quote=True)
        await send_log("🚨 **Critical:** Disk Space Low (Under 500MB). Tasks rejected.")
        return

    if user_id not in ADMINS and batch_temp.ACTIVE_TASKS[user_id] >= MAX_CONCURRENT_TASKS_PER_USER:
        TASK_QUEUE[user_id].append({
            "client": client,
            "message": message_context,
            "data": task_data,
            "delay": delay
        })
        position = len(TASK_QUEUE[user_id])
        await message_context.reply(f"⏳ **Added to Queue:** Position #{position}\nTask will start automatically when your current tasks finish.", quote=True)
        return

    task_uuid = uuid.uuid4().hex
    dest = task_data.get("dest_title", "Direct Message")
    
    batch_temp.ACTIVE_TASKS[user_id] += 1

    start_msg = f"✅ **Task Started!**\nDestination: `{dest}`\nSpeed: `{delay}s` delay\nTask ID: `{task_uuid[:8]}`"
    try:
        if isinstance(message_context, Message):
            if message_context.from_user.is_bot:
                await message_context.edit(start_msg)
            else:
                await message_context.reply(start_msg)
    except Exception: pass
    
    await send_log(f"▶️ **Task Started**\nUser: `{user_id}`\nLink: `{task_data['link'][:40]}...`")

    if user_id not in ACTIVE_PROCESSES:
        ACTIVE_PROCESSES[user_id] = {}
    ACTIVE_PROCESSES[user_id][task_uuid] = {
        "user": task_data.get("dest_title", f"User({user_id})"),
        "dest_title_name": task_data.get("dest_title", "Direct Message"), 
        "item": task_data.get("link", "Unknown"),
        "started": time.time(),
        "total": 0,
        "fetched": 0,
        "success": 0,
        "duplicate": 0,
        "deleted": 0,
        "skipped": 0,
        "filtered": 0,
        "failed": 0,
        "current_status_text": "Starting Batch...",
        "confirming_cancel": False,
        "force_update_ui": False
    }
    
    is_restricted = task_data.get("is_restricted", False)

    targets = [{"dest_id": task_data.get("dest_chat_id"), "dest_thread": task_data.get("dest_thread_id"), "dest_title": dest}]
    asyncio.create_task(
        process_links_logic(
            client,
            message_context,
            task_data["link"],
            targets=targets,
            dest_title=dest, 
            delay=delay,
            acc_user_id=user_id,
            task_uuid=task_uuid,
            is_restricted=is_restricted,
            allowed_types=task_data.get('allowed_types'),
            text_format=task_data.get('text_format', [])
        )
    )   
    
async def process_links_logic(client: Client, message: Message, text: str, targets=None, dest_title="Direct Message", delay=3, acc_user_id=None, task_uuid=None, is_restricted=False, allowed_types=None, text_format=None):
    if text_format is None: text_format = []
    
    user_id = acc_user_id or (message.from_user.id if message.from_user else 0)
    user_mention = message.from_user.mention if getattr(message, "from_user", None) else f"User({user_id})"
    
    if user_id not in ACTIVE_PROCESSES: ACTIVE_PROCESSES[user_id] = {}
    if not task_uuid: task_uuid = uuid.uuid4().hex
    
    if task_uuid not in ACTIVE_PROCESSES[user_id]:
        ACTIVE_PROCESSES[user_id][task_uuid] = {}
        
    task_info = ACTIVE_PROCESSES[user_id][task_uuid]
    task_info.update({
        "user": user_mention, 
        "dest_title_name": dest_title,
        "item": text[:50]+"...", 
        "started": time.time(),
        "total": 0,
        "fetched": 0,
        "success": 0,
        "duplicate": 0,
        "deleted": 0,
        "skipped": 0,
        "filtered": 0,
        "failed": 0,
        "current_status_text": "Starting Batch...",
        "confirming_cancel": False,
        "force_update_ui": False
    })

    if "https://t.me/" in text:
        acc = None
        is_temp_client = False 
        status_message = None
        filter_thread_id = None 
        start_time = time.time()
        source_title = "Unknown Source"
        
        total_count = 0
        success_count = 0
        failed_count = 0
        was_cancelled = False

        try:
            clean_text = text.replace("https://", "").replace("http://", "").replace("t.me/", "").replace("c/", "")
            parts = clean_text.split("/")

            if len(parts) >= 3 and parts[1].isdigit(): 
                filter_thread_id = int(parts[1])

            user_data = await db.get_session(user_id)
            if not user_data:
                await message.reply("**/login First.**")
                return
            
            api_id = await db.get_api_id(user_id) or API_ID
            api_hash = await db.get_api_hash(user_id) or API_HASH
            
            is_temp_client = False
            if user_id in USER_CLIENTS and USER_CLIENTS[user_id].is_connected:
                acc = USER_CLIENTS[user_id]
            else:
                acc = Client(
                    f"task_{user_id}_{task_uuid}", 
                    session_string=user_data, 
                    api_hash=api_hash, 
                    api_id=api_id, 
                    no_updates=True,
                    workers=4,
                    sleep_threshold=60,
                    ipv6=False,
                    in_memory=True
                )
                await acc.start()
                is_temp_client = True

            try: chatid_check = int("-100" + parts[0]) if "https://t.me/c/" in text else parts[0]
            except Exception: chatid_check = parts[0]

            last_segment = parts[-1].strip()
            range_match = re.search(r"(\d+)\s*-\s*([a-zA-Z0-9]+)", text)
            
            if range_match:
                fromID = int(range_match.group(1))
                end_str = range_match.group(2).lower()
                
                if end_str == "all":
                    try:
                        async for last_msg in acc.get_chat_history(chatid_check, limit=1):
                            toID = last_msg.id
                    except Exception as e:
                        print(f"Failed to fetch last message for ALL: {e}")
                        toID = fromID
                else:
                    try: toID = int(end_str)
                    except Exception: toID = fromID
            else:
                fromID = toID = int(last_segment)

            if fromID > toID:
                fromID, toID = toID, fromID

            if fromID == toID:
                try:
                    media_group = await acc.get_media_group(chatid_check, fromID)
                    if media_group and len(media_group) > 1:
                        ids = [m.id for m in media_group]
                        fromID = min(ids)
                        toID = max(ids)
                except Exception:
                    pass

            primary_dest = targets[0]['dest_id'] if targets else "unknown_dest"
            saved_msg_id = await db.get_sync_progress(user_id, chatid_check, primary_dest)

            skip_duplicates_amount = 0
            
            if saved_msg_id >= toID:
                skip_msg = (f"⏭ **DUPLICATE SKIPPED!**\n🤖 **Bot/User:** {user_mention}\n📂 **Source ID:** `{chatid_check}`\n🎯 **Destination:** `{dest_title}`\n✅ **Status:** Files up to ID `{toID}` are already synced.")
                try: await client.send_message(message.chat.id, skip_msg, reply_to_message_id=message.id)
                except Exception: pass
                if task_uuid in ACTIVE_PROCESSES.get(user_id, {}): del ACTIVE_PROCESSES[user_id][task_uuid]
                if is_temp_client: await acc.stop()
                return
                
            elif saved_msg_id >= fromID and saved_msg_id < toID:
                skip_duplicates_amount = (saved_msg_id - fromID) + 1
                fromID = saved_msg_id + 1
                resume_msg = (f"♻️ **AUTO-RESUME ACTIVATED!**\n🤖 **Bot/User:** {user_mention}\n📂 **Source ID:** `{chatid_check}`\n🎯 **Destination:** `{dest_title}`\n▶️ **Resuming From ID:** `{fromID}`")
                try: await client.send_message(message.chat.id, resume_msg, reply_to_message_id=message.id)
                except Exception: pass

            total_count = max(1, toID - fromID + 1)
            
            try:
                source_chat = await acc.get_chat(chatid_check)
                source_title = source_chat.title or "Private Chat"
            except Exception: pass

            task_info.update({
                "source_title": source_title, 
                "total": total_count, 
                "current": 0,
                "fetched": 0,
                "success": 0,
                "duplicate": 0, 
                "deleted": 0,
                "skipped": 0,
                "filtered": 0,
                "failed": 0,
                "current_status_text": "Starting Batch..."
            })

            status_text_header = f"**Batch Task Started!** 🚀\n"
            if filter_thread_id:
                status_text_header += f"**Filter:** `Topic {filter_thread_id} Only` 🎯\n"

            cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Task", callback_data=f"ask_cancel:{task_uuid}")]])

            try:
                initial_ui = generate_aesthetic_progress_ui(task_info, current_status="Starting Batch...", percent=0.0, footer_status="FORWARDING")
                status_message = await client.send_message(
                    message.chat.id,
                    initial_ui,
                    reply_markup=cancel_btn,
                    reply_to_message_id=message.id
                )
                task_info["status_msg_id"] = status_message.id
                task_info["status_chat_id"] = status_message.chat.id
            except Exception as e:
                pass

            last_update_time = time.time()
            inner_header = f"Filter: Topic {filter_thread_id} Only 🎯" if filter_thread_id else ""

            for index, msgid in enumerate(range(fromID, toID+1), start=1):
                loop_start_time = time.time()

                if task_uuid in ACTIVE_PROCESSES.get(user_id, {}):
                    ACTIVE_PROCESSES[user_id][task_uuid]["current"] = index

                if CANCEL_FLAGS.get(task_uuid):
                    was_cancelled = True; break

                is_success = False
                status_reason = "failed"
                try:
                    chat_id = int("-100" + parts[0]) if "https://t.me/c/" in text else parts[0]
                    
                    is_success, status_reason = await handle_private(
                        client, acc, message, chat_id, msgid, index, total_count, 
                        status_message, targets, delay, 
                        user_id, task_uuid, 
                        is_restricted=is_restricted, 
                        header_text=inner_header,
                        filter_thread_id=filter_thread_id,
                        allowed_types=allowed_types,
                        text_format=text_format
                    )
                
                except FloodWait as e:
                    wait_time = e.value + 5
                    wait_msg = f"⏳ **Rate Limiting Detected**\nSleeping for {wait_time} seconds..."
                    try: 
                        if not is_restricted and not task_info.get("confirming_cancel"): await client.edit_message_text(task_info.get("status_chat_id"), task_info.get("status_msg_id"), wait_msg)
                    except Exception: pass
                    await asyncio.sleep(wait_time)
                    continue
                    
                except Exception as e: 
                    print(f"Error processing {msgid}: {e}")
                    status_reason = "failed"

                task_info["fetched"] += 1
                if status_reason == "success": task_info["success"] += 1
                elif status_reason == "deleted": task_info["deleted"] += 1
                elif status_reason == "filtered": task_info["filtered"] += 1
                elif status_reason == "dl_disabled": task_info["skipped"] += 1
                elif status_reason == "skipped": task_info["skipped"] += 1
                elif status_reason == "cancelled": was_cancelled = True; break
                else: task_info["failed"] += 1

                if is_success: success_count += 1
                else: failed_count += 1

                if index < total_count:
                    if is_success:
                        elapsed_time = time.time() - loop_start_time
                        actual_sleep = max(0, delay - elapsed_time)
                        await asyncio.sleep(actual_sleep)
                    else:
                        await asyncio.sleep(0.05)
                        
                if task_info["fetched"] % 100 == 0:
                    gc.collect()

                if not was_cancelled and is_success: 
                    await db.save_sync_progress(user_id, chatid_check, primary_dest, msgid)

                needs_refresh = task_info.get("needs_refresh", False)
                force_update = task_info.get("force_update_ui", False)
                
                if not task_info.get("confirming_cancel"):
                    current_now = time.time()
                    if (task_info["fetched"] % 20 == 0) or msgid == toID or needs_refresh or force_update:
                        task_info["force_update_ui"] = False
                        percent = (task_info["fetched"] / total_count) * 100.0 if total_count > 0 else 0.0
                        
                        eta_str = "..."
                        if task_info["fetched"] > 0:
                            elapsed = current_now - start_time
                            if (task_info["fetched"] / elapsed) > 0:
                                eta_sec = int(((total_count - task_info["fetched"]) / (task_info["fetched"] / elapsed)))
                                eta_str = get_readable_time(eta_sec)
                            
                        fresh_status_ui = generate_aesthetic_progress_ui(
                            task_info, 
                            current_status=f"Forwarding...\n║┣⪼⏳ ETA: {eta_str}", 
                            percent=percent, 
                            footer_status="FORWARDING"
                        )
                        
                        try:
                            active_chat = task_info.get("status_chat_id", message.chat.id)
                            active_msg_id = task_info.get("status_msg_id", status_message.id if status_message else 0)
                            
                            if needs_refresh:
                                try: await client.delete_messages(active_chat, active_msg_id)
                                except Exception: pass
                                new_msg = await client.send_message(message.chat.id, fresh_status_ui, reply_markup=cancel_btn, reply_to_message_id=message.id)
                                task_info["status_msg_id"] = new_msg.id
                                task_info["status_chat_id"] = new_msg.chat.id
                                task_info["needs_refresh"] = False
                            elif active_msg_id:
                                await client.edit_message_text(
                                    chat_id=active_chat,
                                    message_id=active_msg_id,
                                    text=fresh_status_ui,
                                    reply_markup=cancel_btn
                                )
                            last_update_time = current_now
                        except Exception: 
                            pass
                    
        except Exception as e:
            import traceback
            print(f"❌ Task Crashed traceback:\n{traceback.format_exc()}")
            await send_log(f"❌ **Task Crashed**\nUser: `{user_id}`\nError: `{e}`")

        finally:
            if 'was_cancelled' in locals() and was_cancelled:
                footer_tag = "ᴄᴀɴᴄᴇʟʟᴇᴅ"
                status_word = "Cancelled"
                header = f"Batch was Cancelled! 🛑 {user_mention} ✨"
            else:
                footer_tag = "ᴄᴏᴍᴘʟᴇᴛᴇᴅ"
                status_word = "Completed"
                header = f"Batch was Completed! ✅ {user_mention} ✨"

            try:
                tot = task_info.get("total", 1)
                if tot == 0: tot = 1
                fet = task_info.get("fetched", 0)
                final_percent = (fet / tot * 100.0) if tot > 0 else 0.0
                if final_percent > 100.0: final_percent = 100.0
                
                final_status_ui = generate_aesthetic_progress_ui(
                    task_info,
                    current_status=status_word,
                    percent=final_percent,
                    footer_status=footer_tag
                )
                active_chat = task_info.get("status_chat_id", message.chat.id)
                active_msg_id = task_info.get("status_msg_id", status_message.id if status_message else 0)
                if active_msg_id:
                    await client.edit_message_text(chat_id=active_chat, message_id=active_msg_id, text=final_status_ui)
            except Exception: pass

            if task_uuid in ACTIVE_PROCESSES.get(user_id, {}):
                try: del ACTIVE_PROCESSES[user_id][task_uuid]
                except Exception: pass
            
            CANCEL_FLAGS.pop(task_uuid, None)

            batch_temp.ACTIVE_TASKS[user_id] -= 1
            if batch_temp.ACTIVE_TASKS[user_id] < 0: batch_temp.ACTIVE_TASKS[user_id] = 0
            
            if TASK_QUEUE[user_id]:
                next_item = TASK_QUEUE[user_id].pop(0)
                try:
                    asyncio.create_task(start_task_final(next_item["client"], next_item["message"], next_item["data"], next_item["delay"], user_id))
                except Exception as e:
                    print(f"❌ Queue Execution Failed for {user_id}: {e}")
                    batch_temp.ACTIVE_TASKS[user_id] -= 1 

            if acc and is_temp_client: 
                try: await acc.stop()
                except Exception: pass

            duration = time.time() - start_time
            time_taken_str = get_readable_time(int(duration))

            final_text = (
                f"{header}\n"
                f"📝 **Task :** {source_title} → {dest_title}\n"
                f"⏱ **Time Taken:** `{time_taken_str}`\n"
                f"📊 **Statistics:**\n"
                f"├ 📥 **Total Requested:** `{total_count}`\n"
                f"├ ✅ **Successful:** `{success_count}`\n"
                f"└ ❌ **Failed/Skipped:** `{failed_count}`"
            )
            
            try: await client.send_message(message.chat.id, final_text, reply_to_message_id=message.id)
            except Exception: pass

async def handle_private(client: Client, acc, message: Message, chatid, msgid: int, index: int, total_count: int, status_message: Message, targets: list, delay, user_id, task_uuid=None, is_restricted=False, header_text="", filter_thread_id=None, allowed_types=None, text_format=None):
    if not task_uuid:
        task_uuid = "default"
        
    msg = None
    try:
        msg = await acc.get_messages(chatid, msgid)
    except UserNotParticipant: return False, "failed"
    except Exception: return False, "failed"

    if not msg or msg.empty: return False, "deleted"
    
    if filter_thread_id is not None:
        if getattr(msg, "message_thread_id", None) != filter_thread_id:
            return False, "filtered"

    msg_type = get_message_type(msg)
    if not msg_type: return False, "filtered"

    if allowed_types is not None and msg_type not in allowed_types:
        return False, "filtered"

    if task_uuid and CANCEL_FLAGS.get(task_uuid): return False, "cancelled"

    try:
        if status_message:
            m_id = status_message.id
            chat_id = status_message.chat.id
            if f"{chat_id}:{m_id}:down" in PROGRESS: del PROGRESS[f"{chat_id}:{m_id}:down"]
            if f"{chat_id}:{m_id}:up" in PROGRESS: del PROGRESS[f"{chat_id}:{m_id}:up"]
    except Exception: pass

    original_caption = ""
    if msg_type == "Text" and msg.text:
        original_caption = msg.text.html
    elif msg.caption:
        original_caption = msg.caption.html
    else:
        if getattr(msg, "document", None) and getattr(msg.document, "file_name", None): original_caption = msg.document.file_name
        elif getattr(msg, "video", None) and getattr(msg.video, "file_name", None): original_caption = msg.video.file_name
        elif getattr(msg, "audio", None) and getattr(msg.audio, "file_name", None): original_caption = msg.audio.file_name
        elif msg_type == "Photo": original_caption = f"{msgid}.jpg"
        elif msg_type == "Voice": original_caption = f"{msgid}.ogg"

    # 🚀 SAFE SMART CAPTION CLONER (Direct HTML edit, completely safe for formatting)
    media_file_size = (
        getattr(getattr(msg, "document", None), "file_size", 0) or
        getattr(getattr(msg, "video", None), "file_size", 0) or
        getattr(getattr(msg, "audio", None), "file_size", 0) or
        getattr(getattr(msg, "photo", None), "file_size", 0) or
        getattr(getattr(msg, "animation", None), "file_size", 0) or
        getattr(getattr(msg, "voice", None), "file_size", 0) or
        0
    )

    clean_caption = smart_caption(original_caption)
    if not clean_caption.strip() and msg_type in ["Video", "Document", "Audio"]:
        fallback_fn = (
            getattr(getattr(msg, "document", None), "file_name", None) or
            getattr(getattr(msg, "video", None), "file_name", None) or
            getattr(getattr(msg, "audio", None), "file_name", None) or
            ""
        )
        if fallback_fn:
            clean_caption = smart_caption(fallback_fn)

    if text_format:
        if "Bold" in text_format:
            clean_caption = f"<b>{clean_caption}</b>"
        if "Italic" in text_format:
            clean_caption = f"<i>{clean_caption}</i>"

    if msg_type != "Text":
        clean_caption = append_media_footer(clean_caption, media_file_size)

    # 🚀 EXACT TEXT MESSAGE CLONE WITH NEW CAPTION
    if "Text" == msg_type:
        for dest in targets:
            try: 
                await client.send_message(
                    chat_id=dest['dest_id'], 
                    text=clean_caption,
                    reply_markup=msg.reply_markup,
                    reply_to_message_id=dest.get('dest_thread'),
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=True
                )
            except Exception as e_text:
                if "TOO_LONG" in str(e_text):
                    clean_caption = re.sub(r'<[^>]+>', '', clean_caption)[:4000]
                    try: await client.send_message(chat_id=dest['dest_id'], text=clean_caption, reply_markup=msg.reply_markup, reply_to_message_id=dest.get('dest_thread'), disable_web_page_preview=True)
                    except Exception: pass
                else:
                    try:
                        await acc.send_message(
                            chat_id=dest['dest_id'], 
                            text=clean_caption,
                            reply_markup=msg.reply_markup,
                            reply_to_message_id=dest.get('dest_thread'),
                            parse_mode=enums.ParseMode.HTML,
                            disable_web_page_preview=True
                        )
                    except Exception as e_text2:
                        if "TOO_LONG" in str(e_text2):
                            clean_caption = re.sub(r'<[^>]+>', '', clean_caption)[:4000]
                            try: await acc.send_message(chat_id=dest['dest_id'], text=clean_caption, reply_markup=msg.reply_markup, reply_to_message_id=dest.get('dest_thread'), disable_web_page_preview=True)
                            except Exception: pass
        return True, "success"

    original_filename = "unknown_file"
    if getattr(msg, "document", None) and getattr(msg.document, "file_name", None): original_filename = msg.document.file_name
    elif getattr(msg, "video", None) and getattr(msg.video, "file_name", None): original_filename = msg.video.file_name
    elif getattr(msg, "audio", None) and getattr(msg.audio, "file_name", None): original_filename = msg.audio.file_name

    has_dotted_ext = bool(re.search(r'\.[a-zA-Z0-9]{2,4}(?:\.\d{2,4})?$', original_filename))
    auto_dl = await db.get_dl_status()

    # 🚀 FAST FORWARD EXACT CLONE WITH CAPTION REPLACEMENT
    # If the message or chat has explicit Telegram content protection, fast-copy is blocked by Telegram.
    # Otherwise, ALWAYS attempt server-side fast-copy via client/acc FIRST!
    # Even if restricted mode was selected, attempting copy_message takes 0.1s: if Telegram permits it,
    # it finishes in 1 second instead of taking 15 minutes to download/split/upload 4GB!
    is_content_protected = getattr(msg, "has_protected_content", False) or getattr(msg.chat, "has_protected_content", False)
    allow_fast_copy = not is_content_protected
    if allow_fast_copy and (not auto_dl or has_dotted_ext or msg_type not in ["Document", "Video", "Audio"]):
        forward_success = False
        for dest in targets:
            dest_chat_id = dest['dest_id']
            dest_thread_id = dest.get('dest_thread')
            try:
                await client.copy_message(chat_id=dest_chat_id, from_chat_id=chatid, message_id=msgid, reply_to_message_id=dest_thread_id, caption=clean_caption, parse_mode=enums.ParseMode.HTML, reply_markup=msg.reply_markup)
                forward_success = True
            except Exception as e1:
                try:
                    fallback_cap = prepare_caption_fallback(clean_caption, media_file_size)
                    await client.copy_message(chat_id=dest_chat_id, from_chat_id=chatid, message_id=msgid, reply_to_message_id=dest_thread_id, caption=fallback_cap, reply_markup=msg.reply_markup)
                    forward_success = True
                    continue
                except Exception: pass
                try:
                    await acc.copy_message(chat_id=dest_chat_id, from_chat_id=chatid, message_id=msgid, reply_to_message_id=dest_thread_id, caption=clean_caption, parse_mode=enums.ParseMode.HTML, reply_markup=msg.reply_markup)
                    forward_success = True
                except FloodWait as e:
                    await asyncio.sleep(e.value + 5)
                    await acc.copy_message(chat_id=dest_chat_id, from_chat_id=chatid, message_id=msgid, reply_to_message_id=dest_thread_id, caption=clean_caption, parse_mode=enums.ParseMode.HTML, reply_markup=msg.reply_markup)
                    forward_success = True
                except Exception as e2:
                    try:
                        fallback_cap = prepare_caption_fallback(clean_caption, media_file_size)
                        await acc.copy_message(chat_id=dest_chat_id, from_chat_id=chatid, message_id=msgid, reply_to_message_id=dest_thread_id, caption=fallback_cap, reply_markup=msg.reply_markup)
                        forward_success = True
                    except Exception:
                        print(f"Task Fast-Copy blocked: {e2}")
        if forward_success: return True, "success"

    # 🌟 DOWNLOAD TOGGLE SAFETY CHECK
    if not auto_dl:
        if msg_type in ["Video", "Document"]:
            chat_title = getattr(msg.chat, "title", None) or str(chatid)
            await send_log(f"🚫 **File Skipped (Auto-Download OFF)**\n📂 **Source:** `{chat_title}` (`{chatid}`)\n🆔 **Msg ID:** `{msgid}`\n📄 **Type:** `{msg_type}`")
        return False, "dl_disabled"

    task_folder_path = Path(f"./downloads/{user_id}/{task_uuid}/{msgid}/")
    task_folder_path.mkdir(parents=True, exist_ok=True)

    safe_filename = sanitize_filename(original_filename)
    if not safe_filename.strip(): safe_filename = f"{msgid}.dat"
    file_path_to_save = task_folder_path / safe_filename

    chat_for_status = status_message.chat.id if status_message else message.chat.id
    down_task = asyncio.create_task(downstatus(client, status_message, chat_for_status, index, total_count, header_text, task_uuid, user_id))
    file_path = None
    ph_path = None
    download_success = False

    split_limit = 2000 * 1024 * 1024 

    try: 
        for attempt in range(3):
            if task_uuid and CANCEL_FLAGS.get(task_uuid): return False, "cancelled"
            try:
                msg_fresh = await acc.get_messages(chatid, msgid)
                if msg_fresh.empty: return False, "deleted"
                
                file_size = getattr(msg_fresh.document, "file_size", 0) or getattr(msg_fresh.video, "file_size", 0) or getattr(msg_fresh.audio, "file_size", 0)

                if file_size > split_limit:
                    file_path = await acc.download_media(msg_fresh, file_name=str(file_path_to_save), progress=progress, progress_args=[task_uuid, "down"])
                    
                    if down_task and not down_task.done(): down_task.cancel()
                    parts = await split_file_python(file_path, chunk_size=1900*1024*1024)
                    
                    up_task = asyncio.create_task(upstatus(client, status_message, chat_for_status, index, total_count, header_text, task_uuid, user_id))
                    
                    async with USER_SEMAPHORES[user_id]:
                        async with SERVER_UPLOAD_LIMIT:
                            for part in parts:
                                if task_uuid and CANCEL_FLAGS.get(task_uuid): raise Exception("CANCELLED")
                                for dest in targets:
                                    dest_chat_id = dest['dest_id']
                                    dest_thread_id = dest.get('dest_thread')
                                    retry_part = 0
                                    while retry_part < 5: 
                                        try:
                                            await client.send_document(dest_chat_id, str(part), caption=clean_caption, parse_mode=enums.ParseMode.HTML, reply_markup=msg.reply_markup, reply_to_message_id=dest_thread_id, progress=progress, progress_args=[task_uuid, "up"])
                                            break
                                        except FloodWait as e: 
                                            await asyncio.sleep(e.value + 5)
                                        except Exception as e: 
                                            if "CANCELLED" in str(e): raise e
                                            if "CAPTION_TOO_LONG" in str(e):
                                                clean_caption = prepare_caption_fallback(clean_caption, media_file_size)
                                            retry_part += 1
                                            await asyncio.sleep(3)
                                try: os.remove(part)
                                except Exception: pass
                    
                    if up_task and not up_task.done(): up_task.cancel()
                    try: os.remove(file_path)
                    except Exception: pass
                    return True, "success" 
                else:
                    try:
                        file_path = await asyncio.wait_for(
                            acc.download_media(msg_fresh, file_name=str(file_path_to_save), progress=progress, progress_args=[task_uuid, "down"]),
                            timeout=1200
                        )
                    except asyncio.TimeoutError:
                        return False, "failed"
                
                try:
                    thumb = None
                    if msg_fresh.document and msg_fresh.document.thumbs: thumb = msg_fresh.document.thumbs[0]
                    elif msg_fresh.video and msg_fresh.video.thumbs: thumb = msg_fresh.video.thumbs[0]
                    elif msg_fresh.audio and msg_fresh.audio.thumbs: thumb = msg_fresh.audio.thumbs[0]
                    if thumb: ph_path = await acc.download_media(thumb.file_id, file_name=str(task_folder_path / "thumb.jpg"))
                except Exception: pass

                download_success = True
                break
            except FloodWait as e: 
                await asyncio.sleep(e.value + 5)
            except Exception as e:
                if "CANCELLED" in str(e): return False, "cancelled"
                await asyncio.sleep(5)

        if down_task and not down_task.done(): down_task.cancel()
        if not download_success:
            if allow_fast_copy:
                for dest in targets:
                    dest_chat_id = dest['dest_id']
                    dest_thread_id = dest.get('dest_thread')
                    try:
                        await client.copy_message(chat_id=dest_chat_id, from_chat_id=chatid, message_id=msgid, reply_to_message_id=dest_thread_id, caption=clean_caption, parse_mode=enums.ParseMode.HTML, reply_markup=msg.reply_markup)
                        download_success = True
                    except Exception:
                        try:
                            fallback_cap = prepare_caption_fallback(clean_caption, media_file_size)
                            await client.copy_message(chat_id=dest_chat_id, from_chat_id=chatid, message_id=msgid, reply_to_message_id=dest_thread_id, caption=fallback_cap, reply_markup=msg.reply_markup)
                            download_success = True
                        except Exception:
                            pass
                if download_success:
                    return True, "success"
            return False, "failed"
        if task_uuid and CANCEL_FLAGS.get(task_uuid): return False, "cancelled"


        up_task = asyncio.create_task(upstatus(client, status_message, chat_for_status, index, total_count, header_text, task_uuid, user_id))
        
        uploader = client 
        upload_success = False
        
        async def upload_to_dest(dest):
            dest_chat_id = dest['dest_id']
            dest_thread_id = dest.get('dest_thread')
            success_local = False
            async with SERVER_UPLOAD_LIMIT:
                async with USER_SEMAPHORES[user_id]:
                    retry_count = 0
                    while retry_count < 5: 
                        if task_uuid and CANCEL_FLAGS.get(task_uuid): break
                        try:
                            # 🚀 EXACT UPLOAD CLONE WITH CLEAN CAPTION AND DOTTED FILENAME
                            if "Document" == msg_type: await uploader.send_document(dest_chat_id, file_path, thumb=ph_path, caption=clean_caption, file_name=safe_filename, parse_mode=enums.ParseMode.HTML, reply_markup=msg.reply_markup, reply_to_message_id=dest_thread_id, progress=progress, progress_args=[task_uuid,"up"])
                            elif "Video" == msg_type: await uploader.send_video(dest_chat_id, file_path, duration=getattr(msg.video, 'duration', 0), width=getattr(msg.video, 'width', 0), height=getattr(msg.video, 'height', 0), thumb=ph_path, caption=clean_caption, file_name=safe_filename, parse_mode=enums.ParseMode.HTML, reply_markup=msg.reply_markup, reply_to_message_id=dest_thread_id, progress=progress, progress_args=[task_uuid,"up"])
                            elif "Audio" == msg_type: await uploader.send_audio(dest_chat_id, file_path, thumb=ph_path, caption=clean_caption, file_name=safe_filename, parse_mode=enums.ParseMode.HTML, reply_markup=msg.reply_markup, reply_to_message_id=dest_thread_id, progress=progress, progress_args=[task_uuid,"up"])
                            elif "Photo" == msg_type: await uploader.send_photo(dest_chat_id, file_path, caption=clean_caption, parse_mode=enums.ParseMode.HTML, reply_markup=msg.reply_markup, reply_to_message_id=dest_thread_id)
                            elif "Voice" == msg_type: await uploader.send_voice(dest_chat_id, file_path, caption=clean_caption, parse_mode=enums.ParseMode.HTML, reply_markup=msg.reply_markup, reply_to_message_id=dest_thread_id, progress=progress, progress_args=[task_uuid,"up"])
                            elif "Animation" == msg_type: await uploader.send_animation(dest_chat_id, file_path, caption=clean_caption, parse_mode=enums.ParseMode.HTML, reply_markup=msg.reply_markup, reply_to_message_id=dest_thread_id)
                            elif "Sticker" == msg_type: await uploader.send_sticker(dest_chat_id, file_path, reply_markup=msg.reply_markup, reply_to_message_id=dest_thread_id)
                            success_local = True
                            break 
                        except FloodWait as e:
                            await asyncio.sleep(e.value + 5)
                        except Exception as e:
                            if "CANCELLED" in str(e): break
                            if "CAPTION_TOO_LONG" in str(e):
                                clean_caption = prepare_caption_fallback(clean_caption, media_file_size)
                            retry_count += 1
                            await asyncio.sleep(3)
            return success_local

        for dest in targets:
            if task_uuid and CANCEL_FLAGS.get(task_uuid): break
            if await upload_to_dest(dest):
                upload_success = True
        
        if up_task and not up_task.done(): up_task.cancel()
        return (True, "success") if upload_success else (False, "failed")

    finally:
        try: await asyncio.to_thread(shutil.rmtree, task_folder_path, ignore_errors=True)
        except Exception: pass

try:
    from aiohttp import web
except ImportError:
    web = None

async def _koyeb_health_handler(request):
    return web.Response(text="OK", status=200)

async def start_koyeb_health_check(host: str = "0.0.0.0", port: int | str = 8080):
    if web is None:
        print("aiohttp not installed; Koyeb health check not started.")
        return
    try:
        port = int(os.environ.get("PORT", str(port)))
    except Exception:
        port = 8080
    app_web = web.Application()
    app_web.router.add_get("/", _koyeb_health_handler)
    app_web.router.add_get("/health", _koyeb_health_handler)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"Starting Koyeb health check server on port {port}...")

from pyrogram.handlers import MessageHandler

WATCHER_LOCKS = {} 

async def process_watcher_message(client, message):
    chat_id = message.chat.id
    topic_id = getattr(message, "message_thread_id", None)
    
    watcher = await db.get_watcher(chat_id, topic_id)
    if not watcher:
        watcher = await db.get_watcher(chat_id, None)
        
    if not watcher: 
        return

    targets = watcher.get('targets', [])
    if not targets and 'dest_id' in watcher:
        targets = [{"dest_id": watcher['dest_id'], "dest_thread": watcher.get('dest_thread'), "dest_title": watcher.get('dest_title')}]
        
    if not targets: 
        return

    allowed_types = watcher.get('allowed_types', ["Video", "Document"])
    msg_type = get_message_type(message)
    
    if msg_type not in allowed_types: 
        return

    delay = watcher.get('delay', 0)
    owner_id = watcher['user_id']
    is_restricted = watcher.get('is_restricted', False)
    text_format = watcher.get('text_format', [])

    lock_key = f"watcher_{chat_id}_{owner_id}"
    if lock_key not in WATCHER_LOCKS:
        WATCHER_LOCKS[lock_key] = asyncio.Lock()

    async with WATCHER_LOCKS[lock_key]:
        if delay > 0:
            await asyncio.sleep(delay)

        is_content_protected = getattr(message, "has_protected_content", False) or getattr(message.chat, "has_protected_content", False)
        
        fallback_to_download = True 

        if not is_content_protected:
            fallback_to_download = False
            safe_source_id = message.chat.username if message.chat.username else chat_id
            
            # 🚀 EXACT TEXT WATCHER CLONE WITH CLEAN TEXT
            if msg_type == "Text":
                original_caption = message.text.html if message.text else ""
                clean_caption = smart_caption(original_caption)
                if text_format:
                    if "Bold" in text_format: clean_caption = f"<b>{clean_caption}</b>"
                    if "Italic" in text_format: clean_caption = f"<i>{clean_caption}</i>"
                for t in targets:
                    try: 
                        await client.send_message(
                            chat_id=t['dest_id'], 
                            text=clean_caption,
                            reply_markup=message.reply_markup,
                            reply_to_message_id=t.get('dest_thread'),
                            parse_mode=enums.ParseMode.HTML,
                            disable_web_page_preview=True
                        )
                    except Exception as e_text:
                        if "TOO_LONG" in str(e_text):
                            clean_caption = re.sub(r'<[^>]+>', '', clean_caption)[:4000]
                            try: await client.send_message(chat_id=t['dest_id'], text=clean_caption, reply_markup=message.reply_markup, reply_to_message_id=t.get('dest_thread'), disable_web_page_preview=True)
                            except Exception: pass
                return

            original_caption = ""
            if message.caption:
                original_caption = message.caption.html
            else:
                if getattr(message, "document", None) and getattr(message.document, "file_name", None): original_caption = message.document.file_name
                elif getattr(message, "video", None) and getattr(message.video, "file_name", None): original_caption = message.video.file_name
                elif getattr(message, "audio", None) and getattr(message.audio, "file_name", None): original_caption = message.audio.file_name

            media_file_size = (
                getattr(getattr(message, "document", None), "file_size", 0) or
                getattr(getattr(message, "video", None), "file_size", 0) or
                getattr(getattr(message, "audio", None), "file_size", 0) or
                getattr(getattr(message, "photo", None), "file_size", 0) or
                getattr(getattr(message, "animation", None), "file_size", 0) or
                getattr(getattr(message, "voice", None), "file_size", 0) or
                0
            )

            clean_caption = smart_caption(original_caption)
            if not clean_caption.strip() and msg_type in ["Video", "Document", "Audio"]:
                fallback_fn = (
                    getattr(getattr(message, "document", None), "file_name", None) or
                    getattr(getattr(message, "video", None), "file_name", None) or
                    getattr(getattr(message, "audio", None), "file_name", None) or
                    ""
                )
                if fallback_fn:
                    clean_caption = smart_caption(fallback_fn)

            if text_format:
                if "Bold" in text_format: clean_caption = f"<b>{clean_caption}</b>"
                if "Italic" in text_format: clean_caption = f"<i>{clean_caption}</i>"

            clean_caption = append_media_footer(clean_caption, media_file_size)

            original_filename = "unknown_file"
            if getattr(message, "document", None) and getattr(message.document, "file_name", None): original_filename = message.document.file_name
            elif getattr(message, "video", None) and getattr(message.video, "file_name", None): original_filename = message.video.file_name
            elif getattr(message, "audio", None) and getattr(message.audio, "file_name", None): original_filename = message.audio.file_name

            has_dotted_ext = bool(re.search(r'\.[a-zA-Z0-9]{2,4}(?:\.\d{2,4})?$', original_filename))
            auto_dl = await db.get_dl_status()

            fallback_to_download = False
            if auto_dl and not has_dotted_ext and msg_type in ["Document", "Video", "Audio"]:
                fallback_to_download = True
            else:
                for t in targets:
                    success = False
                    dest_id = t['dest_id']
                    dest_thread = t.get('dest_thread')
                    
                    # 🚀 EXACT MEDIA WATCHER FAST FORWARD CLONE
                    try: 
                        await app.copy_message(chat_id=dest_id, from_chat_id=safe_source_id, message_id=message.id, reply_to_message_id=dest_thread, caption=clean_caption, parse_mode=enums.ParseMode.HTML, reply_markup=message.reply_markup)
                        success = True
                    except Exception as e1:
                        try:
                            fallback_cap = prepare_caption_fallback(clean_caption, media_file_size)
                            await app.copy_message(chat_id=dest_id, from_chat_id=safe_source_id, message_id=message.id, reply_to_message_id=dest_thread, caption=fallback_cap, reply_markup=message.reply_markup)
                            success = True
                            continue
                        except Exception: pass
                        try:
                            await client.get_chat(dest_id)
                        except Exception:
                            pass

                        try: 
                            await client.copy_message(chat_id=dest_id, from_chat_id=chat_id, message_id=message.id, reply_to_message_id=dest_thread, caption=clean_caption, parse_mode=enums.ParseMode.HTML, reply_markup=message.reply_markup)
                            success = True
                        except Exception as e2:
                            try:
                                fallback_cap = prepare_caption_fallback(clean_caption, media_file_size)
                                await client.copy_message(chat_id=dest_id, from_chat_id=chat_id, message_id=message.id, reply_to_message_id=dest_thread, caption=fallback_cap, reply_markup=message.reply_markup)
                                success = True
                                continue
                            except Exception: pass
                            try:
                                await client.forward_messages(chat_id=dest_id, from_chat_id=chat_id, message_ids=message.id, message_thread_id=dest_thread)
                                success = True
                            except Exception as e3:

                                await send_log(f"❌ **Fast-Copy Failed!**\nSource: `{chat_id}`\nDest: `{dest_id}`\nError: `{e3}`")
                    
                    if not success:
                        fallback_to_download = True

        if not fallback_to_download:
            return 
                
        owner_client = USER_CLIENTS.get(owner_id)
        if not owner_client: 
            return 

        try:
            source_name = message.chat.title or str(chat_id)
            dest_name = targets[0].get('dest_title', str(targets[0]['dest_id'])) if targets else "Unknown Target"
            
            auto_dl = await db.get_dl_status()
            if not auto_dl:
                alert_text = f"🚫 **Watcher Skipped (Download OFF)**\n📂 **Source:** `{source_name}` (`{chat_id}`)\n🎯 **Dest:** `{dest_name}`\n🆔 **Msg ID:** `{message.id}`\n*(Fast-copy failed, downloading disabled to save bandwidth!)*"
                try: await app.send_message(owner_id, alert_text)
                except Exception: pass
                if msg_type in ["Video", "Document"]:
                    await send_log(alert_text)
                return

            notify_text = f"⬇️ **Watcher Alert (Download Mode)**\n📂 **Source:** `{source_name}`\n🎯 **Dest:** `{dest_name}`\n🆔 **Msg ID:** `{message.id}`\n*(Fast-copy failed, starting manual download...)*"
            try: await app.send_message(owner_id, notify_text)
            except Exception: pass
            
            await send_log(notify_text)

            task_uuid = uuid.uuid4().hex
            if owner_id not in ACTIVE_PROCESSES: 
                ACTIVE_PROCESSES[owner_id] = {}
            ACTIVE_PROCESSES[owner_id][task_uuid] = {
                "user": f"Watcher({owner_id})",
                "dest_title_name": dest_name,
                "item": f"Live Download: ID {message.id}",
                "started": time.time()
            }

            try:
                await handle_private(
                    client=app,
                    acc=owner_client,
                    message=message, 
                    chatid=chat_id, 
                    msgid=message.id, 
                    index=1, 
                    total_count=1, 
                    status_message=dummy_status, 
                    targets=targets,
                    delay=0, 
                    user_id=owner_id, 
                    task_uuid=task_uuid,
                    is_restricted=True,
                    allowed_types=allowed_types,
                    text_format=text_format
                )
            finally:
                if task_uuid in ACTIVE_PROCESSES.get(owner_id, {}):
                    del ACTIVE_PROCESSES[owner_id][task_uuid]
                CANCEL_FLAGS.pop(task_uuid, None)
                
            await dummy_status.delete()
        except Exception as e:
            print(f"❌ [DEBUG WATCHER] Watcher Mode B Fail: {e}")
            
async def user_watcher_handler(client, message):
    await process_watcher_message(client, message)

async def cleanup_startup():
    folder = Path("./downloads")
    if folder.exists():
        try:
            shutil.rmtree(folder)
            print("🧹 Startup: Cleared temporary downloads folder.")
        except Exception as e:
            print(f"⚠️ Could not clean downloads folder: {e}")
    folder.mkdir(parents=True, exist_ok=True)

async def main():
    global USER_CLIENTS
    
    await cleanup_startup()
    asyncio.create_task(cleanup_watchdog())

    await app.start()
    print("🤖 Bot Started")
    try:
        await db.init_indexes()
    except Exception:
        pass
    
    try:
        public_commands = [
            BotCommand("start", "⚡ Check Bot Is Working Or Not"),
            BotCommand("help", "🔎 Check How To Use Bot"),
            BotCommand("login", "📍 Login Your Telegram String Session"),
            BotCommand("logout", "🚨 Logout Your Session"),
            BotCommand("dl", "🦥 Reply to the link to forward"),
            BotCommand("watch", "👀 To live forward"),
            BotCommand("unwatch", "🗑 Stop watching a source"),
            BotCommand("watchers", "📋 List your active watchers"),
            BotCommand("tasks", "📊 View live task progress"),
            BotCommand("toggledl", "🔛 Turn Auto-Download ON/OFF"),
            BotCommand("cancel", "❌ Cancel Your Any Ongoing Task")
        ]

        admin_commands = public_commands + [
            BotCommand("broadcast", "🗞 Broadcast Message"),
            BotCommand("botstats", "🔎 Check User Stats"),
            BotCommand("status", "🦥 Check System Status"),
            BotCommand("log", "📄 Fetch Bot Logs"),
            BotCommand("pixel", "✨ Bypass Pixeldrain Links")
        ]

        await app.set_bot_commands(public_commands, scope=BotCommandScopeDefault())

        all_admins = set(ADMINS + SUDOS)
        
        for admin_id in all_admins:
            try:
                await app.set_bot_commands(
                    admin_commands, 
                    scope=BotCommandScopeChat(chat_id=admin_id)
                )
            except Exception as e:
                pass
                
    except Exception as e:
        pass
    
    active_watcher_users = set()
    cursor = await db.get_all_watchers()
    async for w in cursor:
        active_watcher_users.add(w['user_id'])

    for user_id in active_watcher_users:
        user_session = await db.get_session(user_id)
        if not user_session:
            continue
            
        try:
            if user_id in USER_CLIENTS: continue

            u_api = await db.get_api_id(user_id) or API_ID
            u_hash = await db.get_api_hash(user_id) or API_HASH
            
            user_client = Client(
                f"watcher_main_{user_id}", 
                session_string=user_session, 
                api_id=u_api, 
                api_hash=u_hash, 
                workers=4, 
                ipv6=False,
                in_memory=True
            )
            
            user_client.add_handler(MessageHandler(user_watcher_handler))
            await user_client.start()
            USER_CLIENTS[user_id] = user_client
            
            async for w in db.db.watchers.find({'user_id': user_id}):
                target_source = w['source_id']
                try: 
                    await user_client.get_chat_member(target_source, "me")
                except Exception as e: 
                    pass

            async for w in db.db.watchers.find({'user_id': user_id}):
                try: 
                    await user_client.get_chat(w['source_id'])
                    async for _ in user_client.get_chat_history(w['source_id'], limit=1): break
                except Exception as e: 
                    pass
            
        except Exception as e:
            pass

    asyncio.create_task(start_koyeb_health_check())
    await idle()
    
    await app.stop()
    for uid, client in USER_CLIENTS.items():
        try: await client.stop()
        except Exception: pass
        
if __name__ == "__main__":
    app.run(main())
