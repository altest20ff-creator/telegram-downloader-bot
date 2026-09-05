import os
import re
import time
import shutil
import asyncio
import tempfile
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import requests
import yt_dlp

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.constants import ChatMemberStatus

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# =========================================================
# الإعدادات
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

CHANNEL_USERNAME = "@kingdeveloper2004"

CHANNEL_URL = "https://t.me/kingdeveloper2004"

# أقل من 49MB نرسله مباشرة إلى Telegram
TELEGRAM_LIMIT = 49 * 1024 * 1024

# مجلد التحميل المؤقت
DOWNLOAD_ROOT = Path("/tmp/telegram_downloader")

# تحميل واحد فقط في نفس الوقت لحماية الـ RAM في ريندر
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(1)


# =========================================================
# التحقق من BOT_TOKEN
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN غير موجود. "
        "أضفه في Render Environment Variables."
    )


# =========================================================
# Render Health Check + Self-Ping 24/7
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Telegram Downloader Bot is running 24/7")

    def log_message(self, format, *args):
        return


def self_ping_loop():
    """وظيفة لإرسال طلب ذاتي كل 9 دقائق لمنع Render من الدخول في وضع النوم"""
    time.sleep(15)  # الانتظار حتى اكتمال التشغيل
    
    # Render يضع رابط التطبيق تلقائياً في متغير RENDER_EXTERNAL_URL
    external_url = os.getenv("RENDER_EXTERNAL_URL")
    
    if external_url:
        print(f"🔄 تم تفعيل Self-Ping على الرابط: {external_url}")
        while True:
            try:
                response = requests.get(external_url, timeout=10)
                print(f"🟢 Self-ping status: {response.status_code}")
            except Exception as e:
                print(f"⚠️ Self-ping error: {e}")
            time.sleep(9 * 60)  # كل 9 دقائق (ريندر ينام بعد 15 دقيقة)
    else:
        print("⚠️ لم يتم العثور على RENDER_EXTERNAL_URL. يرجى إضافة رابط الخدمة لـ UptimeRobot لضمان 24/7.")


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health server running on port {port}")

    # تشغيل خادم الصحة في خلفية التطبيق
    t_server = threading.Thread(target=server.serve_forever, daemon=True)
    t_server.start()

    # تشغيل وظيفة المنع من النوم
    t_ping = threading.Thread(target=self_ping_loop, daemon=True)
    t_ping.start()


# =========================================================
# تنظيف المجلد المؤقت عند بدء التشغيل
# =========================================================

def init_environment():
    try:
        if DOWNLOAD_ROOT.exists():
            shutil.rmtree(DOWNLOAD_ROOT, ignore_errors=True)
        DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        print("🧹 تم تنظيف مجلد التحميلات المؤقتة بنجاح.")
    except Exception as e:
        print(f"⚠️ خطأ أثناء تنظيف البيئة: {e}")


# =========================================================
# تحديد نوع الموقع
# =========================================================

def get_platform(url: str):
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower().replace("www.", "")

        if host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be":
            return "youtube"

        if host == "tiktok.com" or host.endswith(".tiktok.com") or host == "vm.tiktok.com":
            return "tiktok"

        if host == "facebook.com" or host.endswith(".facebook.com") or host == "fb.watch":
            return "facebook"

        return None
    except Exception:
        return None


# =========================================================
# التحقق من الرابط
# =========================================================

def clean_url(url: str):
    url = url.strip()
    url = url.replace(" ", "")
    return url


def is_supported_url(url: str):
    platform = get_platform(url)
    return platform is not None


# =========================================================
# cookies.txt
# =========================================================

def find_cookie_file():
    possible_files = [
        Path("/etc/secrets/cookies.txt"),
        Path("/app/cookies.txt"),
        Path("cookies.txt"),
    ]

    for file in possible_files:
        try:
            if file.exists() and file.stat().st_size > 0:
                print(f"Cookie file found: {file}")
                return str(file)
        except Exception:
            pass

    print("No cookies.txt found.")
    return None


# =========================================================
# قائمة الجودة
# =========================================================

def quality_keyboard():
    keyboard = [
        [InlineKeyboardButton("🏆 أفضل جودة", callback_data="quality_best")],
        [
            InlineKeyboardButton("1080p", callback_data="quality_1080"),
            InlineKeyboardButton("720p", callback_data="quality_720"),
        ],
        [
            InlineKeyboardButton("480p", callback_data="quality_480"),
            InlineKeyboardButton("360p", callback_data="quality_360"),
        ],
        [InlineKeyboardButton("🎵 صوت MP3", callback_data="quality_audio")],
    ]
    return InlineKeyboardMarkup(keyboard)


# =========================================================
# شرط الاشتراك
# =========================================================

def subscription_keyboard():
    keyboard = [
        [InlineKeyboardButton("📢 اشترك في القناة", url=CHANNEL_URL)],
        [InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_subscription")],
    ]
    return InlineKeyboardMarkup(keyboard)


# =========================================================
# التحقق من اشتراك المستخدم
# =========================================================

async def is_subscribed(user_id, bot):
    try:
        member = await bot.get_chat_member(
            chat_id=CHANNEL_USERNAME,
            user_id=user_id
        )
        valid_statuses = {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        }
        return member.status in valid_statuses
    except Exception as e:
        print("Subscription check error:", repr(e))
        return False


# =========================================================
# رسالة الاشتراك
# =========================================================

async def send_subscription_message(update: Update):
    await update.message.reply_text(
        "🔒 لاستخدام البوت يجب الاشتراك "
        "في قناتنا أولاً.\n\n"
        "1️⃣ اضغط على «اشترك في القناة»\n"
        "2️⃣ اشترك في القناة\n"
        "3️⃣ اضغط «تحقق من الاشتراك»\n"
        "4️⃣ أرسل الرابط",
        reply_markup=subscription_keyboard()
    )


# =========================================================
# صيغ التحميل
# =========================================================

def get_format(quality: str):
    if quality == "best":
        return "bv*+ba/b"

    if quality == "1080":
        return "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"

    if quality == "720":
        return "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b"

    if quality == "480":
        return "bv*[height<=480]+ba/b[height<=480]/bv*+ba/b"

    if quality == "360":
        return "bv*[height<=360]+ba/b[height<=360]/bv*+ba/b"

    if quality == "audio":
        return "ba/b"

    return "bv*+ba/b"


# =========================================================
# إعدادات yt-dlp
# =========================================================

def create_ydl_options(output_dir: str, quality: str, platform: str, cookie_file=None):
    is_audio = quality == "audio"

    options = {
        "outtmpl": str(Path(output_dir) / "%(title).120s [%(id)s].%(ext)s"),
        "format": get_format(quality),
        "noplaylist": True,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 5,
        "file_access_retries": 5,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 1,
        "quiet": False,
        "no_warnings": False,
        "cachedir": str(Path(output_dir) / "cache"),
        "merge_output_format": "mp4",
        "writethumbnail": False,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "writeinfojson": False,
        "writedescription": False,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/139.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    if platform == "youtube" and cookie_file:
        options["cookiefile"] = cookie_file
        print("YouTube cookies enabled.")

    if is_audio:
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]

    return options


# =========================================================
# البحث عن الملف
# =========================================================

def find_downloaded_file(directory: str):
    directory = Path(directory)
    files = []

    for file in directory.rglob("*"):
        if not file.is_file():
            continue
        if file.name.endswith((".part", ".ytdl", ".json", ".txt")):
            continue
        files.append(file)

    if not files:
        return None

    files.sort(key=lambda x: x.stat().st_size, reverse=True)
    return files[0]


# =========================================================
# تحميل الفيديو
# =========================================================

def download_video(url: str, quality: str):
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)

    temp_dir = tempfile.mkdtemp(prefix="job_", dir=str(DOWNLOAD_ROOT))
    platform = get_platform(url)

    if not platform:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("الموقع غير مدعوم.")

    cookie_file = find_cookie_file() if platform == "youtube" else None

    try:
        options = create_ydl_options(temp_dir, quality, platform, cookie_file)

        print("================================")
        print("Platform:", platform)
        print("Quality:", quality)
        print("URL:", url)
        print("================================")

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")
            duration = info.get("duration")

        print("Download completed:", title)

        output_file = find_downloaded_file(temp_dir)

        if not output_file:
            raise RuntimeError("تم التحميل ولكن لم يتم العثور على الملف النهائي.")

        return {
            "file": str(output_file),
            "title": title,
            "duration": duration,
            "platform": platform,
        }

    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


# =========================================================
# GoFile
# =========================================================

def upload_to_gofile(file_path: str):
    url = "https://upload.gofile.io/uploadfile"

    try:
        with open(file_path, "rb") as file:
            response = requests.post(
                url,
                files={"file": (Path(file_path).name, file, "application/octet-stream")},
                timeout=1800
            )

        response.raise_for_status()
        data = response.json()
        print("GoFile response:", data)

        if data.get("status") == "ok":
            result = data.get("data", {})
            download_page = result.get("downloadPage") or result.get("page")
            if download_page:
                return download_page

        return None
    except Exception as e:
        print("GoFile upload error:", repr(e))
        return None


# =========================================================
# تنظيف الملفات
# =========================================================

def cleanup_file(file_path: str):
    try:
        path = Path(file_path)
        if path.exists():
            shutil.rmtree(path.parent, ignore_errors=True)
    except Exception as e:
        print("Cleanup error:", repr(e))


# =========================================================
# /start
# =========================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    subscribed = await is_subscribed(user.id, context.bot)

    if not subscribed:
        await update.message.reply_text(
            "👋 أهلاً بك في بوت تحميل الفيديوهات.\n\n"
            "🔒 يجب الاشتراك في قناتنا أولاًلاستخدام البوت.",
            reply_markup=subscription_keyboard()
        )
        return

    await update.message.reply_text(
        "👋 أهلاً بك!\n\n"
        "📥 أرسل رابط الفيديو من:\n"
        "• YouTube\n"
        "• TikTok\n"
        "• Facebook\n\n"
        "وسأعطيك خيارات الجودة."
    )


# =========================================================
# استقبال الرابط
# =========================================================

async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    if not user:
        return

    subscribed = await is_subscribed(user.id, context.bot)
    if not subscribed:
        await send_subscription_message(update)
        return

    url = clean_url(update.message.text or "")
    platform = get_platform(url)

    if not platform:
        await update.message.reply_text(
            "❌ الرابط غير مدعوم.\n\n"
            "أرسل رابط من:\n"
            "▶️ YouTube\n"
            "🎵 TikTok\n"
            "📘 Facebook"
        )
        return

    context.user_data["url"] = url
    context.user_data["platform"] = platform

    platform_names = {
        "youtube": "YouTube",
        "tiktok": "TikTok",
        "facebook": "Facebook",
    }

    platform_name = platform_names.get(platform, platform)

    await update.message.reply_text(
        f"✅ تم التعرف على {platform_name}.\n\n🎬 اختر الجودة:",
        reply_markup=quality_keyboard()
    )


# =========================================================
# الأزرار
# =========================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    if query.data == "check_subscription":
        subscribed = await is_subscribed(query.from_user.id, context.bot)
        if subscribed:
            await query.edit_message_text(
                "✅ تم التحقق من اشتراكك.\n\n"
                "أرسل رابط YouTube أو TikTok أو Facebook."
            )
        else:
            await query.edit_message_text(
                "❌ لم يتم العثور على اشتراكك.\n\n"
                "اشترك في القناة ثم اضغط تحقق.",
                reply_markup=subscription_keyboard()
            )
        return

    quality_map = {
        "quality_best": "best",
        "quality_1080": "1080",
        "quality_720": "720",
        "quality_480": "480",
        "quality_360": "360",
        "quality_audio": "audio",
    }

    quality = quality_map.get(query.data)
    if not quality:
        return

    subscribed = await is_subscribed(query.from_user.id, context.bot)
    if not subscribed:
        await query.edit_message_text(
            "🔒 يجب الاشتراك في القناة أولاً.",
            reply_markup=subscription_keyboard()
        )
        return

    url = context.user_data.get("url")
    if not url:
        await query.edit_message_text("❌ انتهت جلسة الرابط.\nأرسل الرابط مرة أخرى.")
        return

    platform = get_platform(url)
    if not platform:
        await query.edit_message_text("❌ الرابط غير مدعوم.")
        return

    quality_names = {
        "best": "🏆 أفضل جودة",
        "1080": "1080p",
        "720": "720p",
        "480": "480p",
        "360": "360p",
        "audio": "🎵 MP3",
    }

    quality_name = quality_names.get(quality, quality)

    await query.edit_message_text(
        "⏳ جاري التحميل...\n\n"
        f"🌐 الموقع: {platform.title()}\n"
        f"🎬 الجودة: {quality_name}\n\n"
        "يرجى الانتظار..."
    )

    async with DOWNLOAD_SEMAPHORE:
        result = None
        try:
            result = await asyncio.to_thread(download_video, url, quality)
            file_path = result["file"]
            title = result["title"]

            file_size = Path(file_path).stat().st_size
            size_mb = file_size / 1024 / 1024

            print(f"Final file size: {size_mb:.2f} MB")

            if file_size <= TELEGRAM_LIMIT:
                await query.message.reply_text("📤 جاري إرسال الملف...")
                try:
                    if quality == "audio":
                        with open(file_path, "rb") as audio:
                            await query.message.reply_audio(audio=audio, title=title[:64])
                    elif file_path.lower().endswith(".mp4"):
                        with open(file_path, "rb") as video:
                            await query.message.reply_video(video=video, supports_streaming=True)
                    else:
                        with open(file_path, "rb") as document:
                            await query.message.reply_document(document=document)
                except Exception as telegram_error:
                    print("Telegram send error:", repr(telegram_error))
                    await query.message.reply_text("⚠️ تعذر الإرسال المباشر.\nجاري رفع الملف إلى GoFile...")
                    link = await asyncio.to_thread(upload_to_gofile, file_path)
                    if link:
                        await query.message.reply_text(f"✅ تم التحميل بنجاح.\n\n🔗 رابط التحميل:\n{link}")
                    else:
                        await query.message.reply_text("❌ فشل رفع الملف إلى GoFile.")
            else:
                await query.message.reply_text(
                    f"📦 حجم الملف كبير ({size_mb:.1f} MB).\n\n☁️ جاري رفعه إلى GoFile..."
                )
                link = await asyncio.to_thread(upload_to_gofile, file_path)
                if link:
                    await query.message.reply_text(
                        f"✅ تم التحميل بنجاح.\n\n📁 الحجم: {size_mb:.1f} MB\n\n🔗 رابط التحميل:\n{link}"
                    )
                else:
                    await query.message.reply_text("❌ تم تحميل الفيديو، لكن فشل رفعه إلى GoFile.")

            cleanup_file(file_path)
            context.user_data.pop("url", None)
            context.user_data.pop("platform", None)

        except Exception as e:
            print("DOWNLOAD ERROR:", repr(e))
            error_text = str(e)
            if len(error_text) > 1500:
                error_text = error_text[-1500:]

            await query.message.reply_text(
                f"❌ تعذر تحميل الفيديو.\n\nآخر خطأ من yt-dlp:\n{error_text}"
            )
            if result:
                try:
                    cleanup_file(result["file"])
                except Exception:
                    pass


# =========================================================
# الأخطاء
# =========================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("BOT ERROR:", repr(context.error))


# =========================================================
# التشغيل
# =========================================================

def main():
    print("================================")
    print("Starting Downloader Bot 24/7...")
    print("YouTube + TikTok + Facebook")
    print("Subscription: @kingdeveloper2004")
    print("================================")

    # تنظيف بيئة التحميلات
    init_environment()

    # تشغيل خادم الصحة للـ Render
    start_health_server()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_error_handler(error_handler)

    print("Bot is running...")

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
