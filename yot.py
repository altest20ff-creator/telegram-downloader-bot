import os
import re
import asyncio
import logging
import tempfile
import shutil
import threading
from pathlib import Path
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, HTTPServer

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

# Telegram Bot API الرسمي يقبل حتى 50MB تقريباً.
# نستخدم 49MB لتجنب مشاكل الحد الأقصى.
TELEGRAM_LIMIT = 49 * 1024 * 1024

# مجلد مؤقت للتحميل
BASE_TEMP_DIR = Path("/tmp/telegram_downloader")

# رابط GoFile
GOFILE_UPLOAD_URL = "https://upload.gofile.io/uploadfile"

# منع عدة تحميلات ثقيلة في نفس الوقت
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(1)


# =========================================================
# Logging
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("telegram-downloader")


# =========================================================
# التحقق من BOT_TOKEN
# =========================================================

if not BOT_TOKEN:
    logger.warning("BOT_TOKEN is not set.")


# =========================================================
# Health Check لـ Render
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Telegram Downloader Bot is running.")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler,
    )

    logger.info("Health server running on port %s", port)

    server.serve_forever()


# =========================================================
# إنشاء مجلد مؤقت
# =========================================================

def ensure_temp_dir():
    BASE_TEMP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


# =========================================================
# اكتشاف المنصة
# =========================================================

def get_platform(url: str):
    try:
        parsed = urlparse(url.strip())
        host = parsed.netloc.lower().split(":")[0]

        if host.startswith("www."):
            host = host[4:]

        # YouTube
        if (
            host == "youtube.com"
            or host.endswith(".youtube.com")
            or host == "youtu.be"
        ):
            return "YouTube"

        # TikTok
        if (
            host == "tiktok.com"
            or host.endswith(".tiktok.com")
        ):
            return "TikTok"

        # Facebook
        if (
            host == "facebook.com"
            or host.endswith(".facebook.com")
            or host == "fb.watch"
        ):
            return "Facebook"

    except Exception:
        pass

    return None


# =========================================================
# التحقق من الرابط
# =========================================================

URL_PATTERN = re.compile(
    r"https?://[^\s<>\"]+",
    re.IGNORECASE,
)


def extract_url(text: str):
    if not text:
        return None

    match = URL_PATTERN.search(text)

    if not match:
        return None

    url = match.group(0).strip()

    # إزالة علامات شائعة إذا كانت بعد الرابط
    url = url.rstrip(".,!?؛،)]}")

    return url


# =========================================================
# ملف Cookies الخاص بـ YouTube
# =========================================================

def find_cookie_file():
    possible_files = [
        Path("/etc/secrets/cookies.txt"),
        Path("/app/cookies.txt"),
        Path("cookies.txt"),
    ]

    for file_path in possible_files:
        if file_path.is_file():
            logger.info("YouTube cookies found: %s", file_path)
            return str(file_path)

    logger.info("No cookies.txt found.")
    return None


# =========================================================
# خيارات الجودة
# =========================================================

QUALITY_OPTIONS = {
    "best": "أفضل جودة",
    "1080": "1080p",
    "720": "720p",
    "480": "480p",
    "360": "360p",
    "mp3": "MP3",
}


def get_format(quality: str):

    if quality == "best":
        return "bv*+ba/b"

    if quality == "1080":
        return (
            "bv*[height<=1080]+ba/"
            "b[height<=1080]/"
            "bv*+ba/b"
        )

    if quality == "720":
        return (
            "bv*[height<=720]+ba/"
            "b[height<=720]/"
            "bv*+ba/b"
        )

    if quality == "480":
        return (
            "bv*[height<=480]+ba/"
            "b[height<=480]/"
            "bv*+ba/b"
        )

    if quality == "360":
        return (
            "bv*[height<=360]+ba/"
            "b[height<=360]/"
            "bv*+ba/b"
        )

    if quality == "mp3":
        return "ba/b"

    return "bv*+ba/b"


# =========================================================
# إنشاء خيارات yt-dlp
# =========================================================

def build_ydl_options(
    quality: str,
    output_dir: str,
    platform: str,
):

    options = {
        "format": get_format(quality),

        # تحميل فيديو واحد فقط
        "noplaylist": True,

        # إعادة المحاولة
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 5,
        "file_access_retries": 5,

        # الشبكة
        "socket_timeout": 30,

        # لا نضغط على السيرفر بعدة أجزاء
        "concurrent_fragment_downloads": 1,

        # الكاش داخل المجلد المؤقت
        "cachedir": os.path.join(output_dir, "cache"),

        # دمج الفيديو والصوت في MP4 عندما يكون ذلك ممكناً
        "merge_output_format": "mp4",

        # اسم ملف بسيط وآمن
        "outtmpl": os.path.join(
            output_dir,
            "%(id)s.%(ext)s",
        ),

        # User-Agent
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        },

        # دعم تحديات JavaScript الحديثة في YouTube
        "js_runtimes": {
            "deno": {}
        },

        # لا نحتاج صور مصغرة أو معلومات إضافية
        "writethumbnail": False,
        "writeinfojson": False,
        "writedescription": False,

        # لا نحمل الترجمة
        "writesubtitles": False,
        "writeautomaticsub": False,

        # عدم إظهار الكثير من الرسائل
        "quiet": True,
        "no_warnings": True,

        # عدم تحميل Playlist
        "extract_flat": False,
    }

    # =====================================================
    # Cookies فقط لـ YouTube
    # =====================================================

    if platform == "YouTube":

        cookie_file = find_cookie_file()

        if cookie_file:
            options["cookiefile"] = cookie_file

    # =====================================================
    # MP3
    # =====================================================

    if quality == "mp3":

        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]

    return options


# =========================================================
# تحميل الفيديو
# =========================================================

def download_video_sync(
    url: str,
    quality: str,
    platform: str,
    output_dir: str,
):

    options = build_ydl_options(
        quality=quality,
        output_dir=output_dir,
        platform=platform,
    )

    logger.info(
        "Starting download: platform=%s quality=%s url=%s",
        platform,
        quality,
        url,
    )

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=True,
        )

        title = info.get("title") or "video"

        duration = info.get("duration")

    # البحث عن الملف الناتج
    files = []

    for path in Path(output_dir).rglob("*"):

        if not path.is_file():
            continue

        if path.name.endswith(".part"):
            continue

        if path.name.endswith(".ytdl"):
            continue

        if path.name.endswith(".tmp"):
            continue

        files.append(path)

    if not files:
        raise RuntimeError(
            "لم يتم العثور على الملف بعد انتهاء التحميل."
        )

    # أكبر ملف غالباً هو الفيديو النهائي
    output_file = max(
        files,
        key=lambda p: p.stat().st_size,
    )

    return {
        "file": str(output_file),
        "title": title,
        "duration": duration,
        "platform": platform,
    }


async def download_video(
    url: str,
    quality: str,
    platform: str,
    output_dir: str,
):

    async with DOWNLOAD_SEMAPHORE:

        return await asyncio.to_thread(
            download_video_sync,
            url,
            quality,
            platform,
            output_dir,
        )


# =========================================================
# GoFile
# =========================================================

def upload_to_gofile_sync(file_path: str):

    file_path_obj = Path(file_path)

    if not file_path_obj.exists():
        raise FileNotFoundError(
            "الملف غير موجود."
        )

    logger.info(
        "Uploading to GoFile: %s (%d bytes)",
        file_path_obj,
        file_path_obj.stat().st_size,
    )

    with open(
        file_path_obj,
        "rb",
    ) as file_handle:

        response = requests.post(
            GOFILE_UPLOAD_URL,
            files={
                "file": (
                    file_path_obj.name,
                    file_handle,
                )
            },
            timeout=3600,
        )

    response.raise_for_status()

    data = response.json()

    logger.info(
        "GoFile response: %s",
        data,
    )

    # الشكل المعتاد:
    # {
    #   "status": "ok",
    #   "data": {
    #       "downloadPage": "..."
    #   }
    # }

    if data.get("status") != "ok":
        raise RuntimeError(
            f"GoFile upload failed: {data}"
        )

    result = data.get("data") or {}

    link = (
        result.get("downloadPage")
        or result.get("page")
    )

    if not link:
        raise RuntimeError(
            "تم رفع الملف ولكن لم يتم الحصول على رابط GoFile."
        )

    return link


async def upload_to_gofile(file_path: str):

    return await asyncio.to_thread(
        upload_to_gofile_sync,
        file_path,
    )


# =========================================================
# زر الاشتراك
# =========================================================

def subscription_keyboard():

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 الاشتراك في القناة",
                url=CHANNEL_URL,
            )
        ],
        [
            InlineKeyboardButton(
                "✅ تحقق من الاشتراك",
                callback_data="check_subscription",
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# التحقق من الاشتراك
# =========================================================

async def is_subscribed(
    bot,
    user_id: int,
):

    try:

        member = await bot.get_chat_member(
            chat_id=CHANNEL_USERNAME,
            user_id=user_id,
        )

        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )

    except Exception as error:

        logger.error(
            "Subscription check error: %s",
            error,
        )

        return False


# =========================================================
# رسالة الاشتراك
# =========================================================

async def send_subscription_required(
    message,
):

    await message.reply_text(
        "⚠️ يجب الاشتراك في القناة أولاً لاستخدام البوت.\n\n"
        "1️⃣ اضغط على «الاشتراك في القناة»\n"
        "2️⃣ اشترك في القناة\n"
        "3️⃣ اضغط «تحقق من الاشتراك»",
        reply_markup=subscription_keyboard(),
    )


# =========================================================
# /start
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_user:
        return

    if not update.message:
        return

    subscribed = await is_subscribed(
        context.bot,
        update.effective_user.id,
    )

    if not subscribed:

        await send_subscription_required(
            update.message
        )

        return

    await update.message.reply_text(
        "👋 أهلاً بك!\n\n"
        "🎬 أرسل رابط فيديو من:\n"
        "• YouTube\n"
        "• TikTok\n"
        "• Facebook\n\n"
        "ثم اختر الجودة التي تريدها."
    )


# =========================================================
# أزرار الجودة
# =========================================================

def quality_keyboard():

    keyboard = [
        [
            InlineKeyboardButton(
                "🏆 أفضل جودة",
                callback_data="quality_best",
            ),
        ],
        [
            InlineKeyboardButton(
                "1080p",
                callback_data="quality_1080",
            ),
            InlineKeyboardButton(
                "720p",
                callback_data="quality_720",
            ),
        ],
        [
            InlineKeyboardButton(
                "480p",
                callback_data="quality_480",
            ),
            InlineKeyboardButton(
                "360p",
                callback_data="quality_360",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎵 MP3",
                callback_data="quality_mp3",
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# استقبال الرابط
# =========================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_user:
        return

    if not update.message:
        return

    text = update.message.text or ""

    url = extract_url(text)

    if not url:

        await update.message.reply_text(
            "❌ أرسل رابط فيديو صحيح."
        )

        return

    platform = get_platform(url)

    if not platform:

        await update.message.reply_text(
            "❌ هذا الرابط غير مدعوم.\n\n"
            "المنصات المدعومة:\n"
            "• YouTube\n"
            "• TikTok\n"
            "• Facebook"
        )

        return

    # التحقق من الاشتراك
    subscribed = await is_subscribed(
        context.bot,
        update.effective_user.id,
    )

    if not subscribed:

        await send_subscription_required(
            update.message
        )

        return

    # حفظ بيانات الرابط مؤقتاً للمستخدم
    context.user_data["download_url"] = url
    context.user_data["platform"] = platform

    await update.message.reply_text(
        f"✅ تم التعرف على الرابط.\n\n"
        f"🌐 المنصة: {platform}\n\n"
        "اختر الجودة:",
        reply_markup=quality_keyboard(),
    )


# =========================================================
# معالجة زر الجودة
# =========================================================

async def handle_quality_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    user = update.effective_user

    if not user:
        return

    callback_data = query.data or ""

    if not callback_data.startswith("quality_"):
        return

    quality = callback_data.replace(
        "quality_",
        "",
        1,
    )

    if quality not in QUALITY_OPTIONS:
        return

    url = context.user_data.get("download_url")
    platform = context.user_data.get("platform")

    if not url or not platform:

        await query.edit_message_text(
            "❌ انتهت جلسة الرابط.\n"
            "أرسل الرابط مرة أخرى."
        )

        return

    # التحقق مرة ثانية قبل بدء التحميل
    subscribed = await is_subscribed(
        context.bot,
        user.id,
    )

    if not subscribed:

        await query.edit_message_text(
            "⚠️ يجب الاشتراك في القناة أولاً.",
            reply_markup=subscription_keyboard(),
        )

        return

    quality_name = QUALITY_OPTIONS[quality]

    try:

        await query.edit_message_text(
            "⏳ جاري تجهيز التحميل...\n\n"
            f"🌐 المنصة: {platform}\n"
            f"🎞 الجودة: {quality_name}"
        )

        ensure_temp_dir()

        # مجلد خاص لهذا التحميل
        job_dir = tempfile.mkdtemp(
            prefix="job_",
            dir=str(BASE_TEMP_DIR),
        )

        try:

            result = await download_video(
                url=url,
                quality=quality,
                platform=platform,
                output_dir=job_dir,
            )

            file_path = Path(
                result["file"]
            )

            if not file_path.exists():

                raise RuntimeError(
                    "ملف التحميل غير موجود."
                )

            file_size = file_path.stat().st_size

            file_size_mb = file_size / (
                1024 * 1024
            )

            title = result["title"]

            logger.info(
                "Downloaded: %s - %.2f MB",
                title,
                file_size_mb,
            )

            # =================================================
            # إرسال مباشر إلى Telegram
            # =================================================

            if file_size <= TELEGRAM_LIMIT:

                await query.edit_message_text(
                    "📤 جاري إرسال الفيديو إلى Telegram..."
                )

                caption = (
                    f"🎬 {title}\n\n"
                    f"🌐 {platform}\n"
                    f"🎞 {quality_name}"
                )

                if quality == "mp3":

                    with open(
                        file_path,
                        "rb",
                    ) as audio:

                        await context.bot.send_audio(
                            chat_id=update.effective_chat.id,
                            audio=audio,
                            caption=caption,
                            title=title[:64],
                        )

                elif file_path.suffix.lower() == ".mp4":

                    with open(
                        file_path,
                        "rb",
                    ) as video:

                        await context.bot.send_video(
                            chat_id=update.effective_chat.id,
                            video=video,
                            caption=caption,
                            supports_streaming=True,
                        )

                else:

                    with open(
                        file_path,
                        "rb",
                    ) as document:

                        await context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=document,
                            caption=caption,
                        )

                await query.edit_message_text(
                    "✅ تم إرسال الملف بنجاح."
                )

            # =================================================
            # GoFile للملفات الكبيرة
            # =================================================

            else:

                await query.edit_message_text(
                    f"📦 حجم الملف: {file_size_mb:.1f} MB\n\n"
                    "📤 حجم الملف أكبر من حد Telegram المباشر.\n"
                    "⏳ جاري رفعه إلى GoFile..."
                )

                gofile_link = await upload_to_gofile(
                    str(file_path)
                )

                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "⬇️ تحميل الفيديو",
                                url=gofile_link,
                            )
                        ]
                    ]
                )

                await query.edit_message_text(
                    f"✅ تم رفع الفيديو بنجاح.\n\n"
                    f"🎬 {title}\n"
                    f"📦 الحجم: {file_size_mb:.1f} MB\n\n"
                    "اضغط الزر بالأسفل لفتح صفحة تحميل الفيديو:",
                    reply_markup=keyboard,
                )

        finally:

            # حذف مجلد التحميل بالكامل
            try:

                shutil.rmtree(
                    job_dir,
                    ignore_errors=True,
                )

                logger.info(
                    "Temporary files deleted."
                )

            except Exception as cleanup_error:

                logger.error(
                    "Cleanup error: %s",
                    cleanup_error,
                )

    except Exception as error:

        logger.exception(
            "Download error"
        )

        error_text = str(error)

        # رسائل مفهومة للمستخدم
        if "Sign in to confirm" in error_text:

            user_message = (
                "❌ YouTube يطلب التحقق من الحساب أو أنك لست روبوتاً.\n\n"
                "تأكد من أن cookies.txt حديث وصحيح."
            )

        elif "Requested format is not available" in error_text:

            user_message = (
                "❌ الجودة المطلوبة غير متاحة لهذا الفيديو.\n"
                "جرّب جودة أخرى."
            )

        elif "Unsupported URL" in error_text:

            user_message = (
                "❌ الرابط غير مدعوم أو غير صحيح."
            )

        elif "Video unavailable" in error_text:

            user_message = (
                "❌ الفيديو غير متاح أو خاص."
            )

        else:

            user_message = (
                "❌ حدث خطأ أثناء التحميل.\n\n"
                "جرّب مرة أخرى أو اختر جودة أخرى."
            )

        try:

            await query.edit_message_text(
                user_message
            )

        except Exception:

            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=user_message,
            )


# =========================================================
# زر التحقق من الاشتراك
# =========================================================

async def check_subscription_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    user = update.effective_user

    if not user:
        return

    subscribed = await is_subscribed(
        context.bot,
        user.id,
    )

    if subscribed:

        await query.edit_message_text(
            "✅ تم التحقق من اشتراكك.\n\n"
            "🎬 الآن أرسل رابط الفيديو."
        )

    else:

        await query.edit_message_text(
            "❌ لم يتم العثور على اشتراكك في القناة.\n\n"
            "اشترك أولاً ثم اضغط على زر التحقق.",
            reply_markup=subscription_keyboard(),
        )


# =========================================================
# Error Handler
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.exception(
        "Unhandled exception:",
        exc_info=context.error,
    )


# =========================================================
# تشغيل البوت
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN غير موجود. "
            "أضفه في Render Environment Variables."
        )

    ensure_temp_dir()

    # تشغيل Health Check في Thread
    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True,
    )

    health_thread.start()

    # إنشاء التطبيق
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # /start
    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    # أزرار التحقق والجودة
    application.add_handler(
        CallbackQueryHandler(
            check_subscription_callback,
            pattern=r"^check_subscription$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            handle_quality_callback,
            pattern=r"^quality_(best|1080|720|480|360|mp3)$",
        )
    )

    # استقبال الروابط والنصوص
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    # معالجة الأخطاء
    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Bot started successfully."
    )

    # Polling
    application.run_polling(
        drop_pending_updates=True,
    )


# =========================================================
# Entry Point
# =========================================================

if __name__ == "__main__":
    main()
