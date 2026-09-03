import os
import re
import shutil
import asyncio
import tempfile
import threading
from pathlib import Path
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

CHANNEL_USERNAME = os.getenv(
    "CHANNEL_USERNAME",
    "@kingdeveloper2004"
).strip()

CHANNEL_URL = os.getenv(
    "CHANNEL_URL",
    "https://t.me/kingdeveloper2004"
).strip()

# Telegram يضع حدًا يقارب 50MB للرفع المباشر حاليًا.
# نستخدم 49MB حتى يكون لدينا هامش أمان.
TELEGRAM_LIMIT = 49 * 1024 * 1024

# مجلد مؤقت
DOWNLOAD_ROOT = Path("/tmp/telegram_downloader")

# لا نسمح بأكثر من تحميل في نفس الوقت
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(1)


# =========================================================
# التحقق من الإعدادات
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN غير موجود. أضفه في Render Environment Variables."
    )


# =========================================================
# Health Check لـ Render
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"Health server running on port {port}")

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True
    )

    thread.start()


# =========================================================
# العثور على cookies.txt
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
                print(f"Using cookies file: {file}")
                return str(file)
        except Exception:
            pass

    print("No cookies.txt found.")
    return None


# =========================================================
# تنظيف الرابط
# =========================================================

def clean_url(url: str) -> str:
    url = url.strip()

    # إزالة المسافات
    url = url.replace(" ", "")

    return url


def is_supported_url(url: str) -> bool:
    patterns = [
        r"youtube\.com",
        r"youtu\.be",
        r"youtube-nocookie\.com",
    ]

    return any(
        re.search(pattern, url, re.IGNORECASE)
        for pattern in patterns
    )


# =========================================================
# الأزرار
# =========================================================

def quality_keyboard():

    keyboard = [
        [
            InlineKeyboardButton(
                "أفضل جودة",
                callback_data="quality_best"
            )
        ],
        [
            InlineKeyboardButton(
                "1080p",
                callback_data="quality_1080"
            ),
            InlineKeyboardButton(
                "720p",
                callback_data="quality_720"
            ),
        ],
        [
            InlineKeyboardButton(
                "480p",
                callback_data="quality_480"
            ),
            InlineKeyboardButton(
                "360p",
                callback_data="quality_360"
            ),
        ],
        [
            InlineKeyboardButton(
                "🎵 صوت فقط",
                callback_data="quality_audio"
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# صيغ التحميل
# =========================================================

def get_format(quality: str):

    if quality == "best":
        return (
            "bv*+ba/b"
        )

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

    if quality == "audio":
        return "ba/b"

    return "bv*+ba/b"


# =========================================================
# إعدادات yt-dlp
# =========================================================

def create_ydl_options(
    output_dir: str,
    quality: str,
    cookie_file=None
):

    is_audio = quality == "audio"

    options = {
        "outtmpl": str(
            Path(output_dir) /
            "%(title).120s [%(id)s].%(ext)s"
        ),

        "format": get_format(quality),

        "noplaylist": True,

        # محاولات إضافية
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 5,

        "file_access_retries": 5,

        # مهلة الاتصال
        "socket_timeout": 30,

        # لا نحمل عدة أجزاء في نفس الوقت
        # حتى لا نستهلك RAM/CPU في Render
        "concurrent_fragment_downloads": 1,

        # إظهار معلومات مفيدة في Logs
        "quiet": False,
        "no_warnings": False,

        # لا نريد حفظ cache ضخم
        "cachedir": str(
            Path(output_dir) / "cache"
        ),

        # محاولة دمج الفيديو والصوت بدون إعادة ترميز
        "merge_output_format": "mp4/mkv",

        # لا نحمل قائمة تشغيل كاملة
        "playlistend": 1,

        # عدم تنزيل الصور أو الترجمة أو أي ملفات إضافية
        "writethumbnail": False,
        "writesubtitles": False,
        "writeautomaticsub": False,

        # EJS / JavaScript
        # Deno مثبت في Dockerfile
        "js_runtimes": {
            "deno": {}
        },

        # Headers طبيعية
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/139.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    # =====================================================
    # Cookies
    # =====================================================

    if cookie_file:
        options["cookiefile"] = cookie_file

    # =====================================================
    # الصوت
    # =====================================================

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
# البحث عن الملف الناتج
# =========================================================

def find_downloaded_file(directory: str):

    directory = Path(directory)

    files = []

    for file in directory.rglob("*"):

        if not file.is_file():
            continue

        # تجاهل الملفات المؤقتة
        if file.name.endswith(".part"):
            continue

        if file.name.endswith(".ytdl"):
            continue

        if file.name.endswith(".json"):
            continue

        if file.name.endswith(".txt"):
            continue

        files.append(file)

    if not files:
        return None

    # أكبر ملف غالبًا هو الفيديو/الصوت الحقيقي
    files.sort(
        key=lambda x: x.stat().st_size,
        reverse=True
    )

    return files[0]


# =========================================================
# GoFile
# =========================================================

def upload_to_gofile(file_path: str):

    url = "https://upload.gofile.io/uploadfile"

    try:

        with open(file_path, "rb") as file:

            response = requests.post(
                url,
                files={
                    "file": (
                        Path(file_path).name,
                        file,
                        "application/octet-stream"
                    )
                },
                timeout=1800
            )

        response.raise_for_status()

        data = response.json()

        print("GoFile response:", data)

        # الشكل الحالي المعتاد
        if data.get("status") == "ok":

            result = data.get("data", {})

            download_page = result.get(
                "downloadPage"
            )

            if download_page:
                return download_page

            # احتياط
            page = result.get("page")

            if page:
                return page

        return None

    except Exception as e:

        print(
            "GoFile upload error:",
            repr(e)
        )

        return None


# =========================================================
# التحميل
# =========================================================

def download_video(
    url: str,
    quality: str
):

    DOWNLOAD_ROOT.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_dir = tempfile.mkdtemp(
        prefix="job_",
        dir=str(DOWNLOAD_ROOT)
    )

    cookie_file = find_cookie_file()

    try:

        options = create_ydl_options(
            temp_dir,
            quality,
            cookie_file
        )

        print(
            f"Starting download: "
            f"{url} | quality={quality}"
        )

        with yt_dlp.YoutubeDL(options) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

            title = info.get(
                "title",
                "video"
            )

            duration = info.get(
                "duration"
            )

            print(
                f"Downloaded: {title}"
            )

        output_file = find_downloaded_file(
            temp_dir
        )

        if not output_file:
            raise RuntimeError(
                "لم يتم العثور على الملف بعد التحميل."
            )

        return {
            "file": str(output_file),
            "title": title,
            "duration": duration,
        }

    except Exception:

        # تنظيف عند الخطأ
        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )

        raise


# =========================================================
# حذف المجلد المؤقت
# =========================================================

def cleanup_file(file_path: str):

    try:

        path = Path(file_path)

        if path.exists():

            parent = path.parent

            shutil.rmtree(
                parent,
                ignore_errors=True
            )

    except Exception as e:

        print(
            "Cleanup error:",
            repr(e)
        )


# =========================================================
# /start
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "👋 أهلاً بك في بوت تحميل الفيديوهات.\n\n"
        "أرسل رابط فيديو YouTube وسأعطيك خيارات الجودة."
    )

    await update.message.reply_text(
        text
    )


# =========================================================
# التحقق من الاشتراك
# =========================================================

async def is_subscribed(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return False

    try:

        member = await context.bot.get_chat_member(
            chat_id=CHANNEL_USERNAME,
            user_id=user.id
        )

        valid_statuses = {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        }

        return member.status in valid_statuses

    except Exception as e:

        print(
            "Subscription check error:",
            repr(e)
        )

        return False


# =========================================================
# رسالة الاشتراك
# =========================================================

async def send_subscription_message(
    update: Update
):

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📢 اشترك في القناة",
                    url=CHANNEL_URL
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ تحققت من الاشتراك",
                    callback_data="check_subscription"
                )
            ],
        ]
    )

    await update.message.reply_text(
        "⚠️ يجب الاشتراك في القناة أولاً.",
        reply_markup=keyboard
    )


# =========================================================
# استقبال الرابط
# =========================================================

async def receive_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    url = clean_url(
        update.message.text or ""
    )

    # التحقق من الاشتراك
    subscribed = await is_subscribed(
        update,
        context
    )

    if not subscribed:

        await send_subscription_message(
            update
        )

        return

    # التحقق من الرابط
    if not is_supported_url(url):

        await update.message.reply_text(
            "❌ أرسل رابط YouTube صحيح."
        )

        return

    # حفظ الرابط مؤقتًا لهذا المستخدم
    context.user_data["url"] = url

    await update.message.reply_text(
        "🎬 اختر الجودة:",
        reply_markup=quality_keyboard()
    )


# =========================================================
# Callback
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    # =============================================
    # التحقق من الاشتراك
    # =============================================

    if query.data == "check_subscription":

        try:

            member = await context.bot.get_chat_member(
                chat_id=CHANNEL_USERNAME,
                user_id=query.from_user.id
            )

            valid_statuses = {
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            }

            if member.status in valid_statuses:

                await query.edit_message_text(
                    "✅ تم التحقق.\n"
                    "أرسل رابط YouTube الآن."
                )

            else:

                await query.edit_message_text(
                    "❌ لم يتم العثور على اشتراكك.\n"
                    "اشترك أولاً ثم اضغط تحقق.",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "📢 اشترك",
                                    url=CHANNEL_URL
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    "✅ تحقق",
                                    callback_data="check_subscription"
                                )
                            ],
                        ]
                    )
                )

        except Exception as e:

            print(
                "Callback subscription error:",
                repr(e)
            )

            await query.edit_message_text(
                "❌ تعذر التحقق حاليًا. "
                "تأكد أن البوت مشرف في القناة."
            )

        return

    # =============================================
    # الجودة
    # =============================================

    quality_map = {
        "quality_best": "best",
        "quality_1080": "1080",
        "quality_720": "720",
        "quality_480": "480",
        "quality_360": "360",
        "quality_audio": "audio",
    }

    quality = quality_map.get(
        query.data
    )

    if not quality:
        return

    url = context.user_data.get(
        "url"
    )

    if not url:

        await query.edit_message_text(
            "❌ انتهت جلسة الرابط.\n"
            "أرسل الرابط مرة أخرى."
        )

        return

    quality_names = {
        "best": "أفضل جودة",
        "1080": "1080p",
        "720": "720p",
        "480": "480p",
        "360": "360p",
        "audio": "صوت MP3",
    }

    quality_name = quality_names.get(
        quality,
        quality
    )

    await query.edit_message_text(
        f"⏳ جاري التحميل...\n"
        f"الجودة: {quality_name}\n\n"
        f"قد يستغرق ذلك بعض الوقت."
    )

    # =============================================
    # تحميل واحد فقط
    # =============================================

    async with DOWNLOAD_SEMAPHORE:

        try:

            result = await asyncio.to_thread(
                download_video,
                url,
                quality
            )

            file_path = result["file"]
            title = result["title"]

            file_size = Path(
                file_path
            ).stat().st_size

            print(
                f"File size: {file_size / 1024 / 1024:.2f} MB"
            )

            # =====================================
            # Telegram
            # =====================================

            if file_size <= TELEGRAM_LIMIT:

                await query.message.reply_text(
                    "📤 جاري إرسال الملف إلى Telegram..."
                )

                try:

                    if quality == "audio":

                        with open(
                            file_path,
                            "rb"
                        ) as audio_file:

                            await query.message.reply_audio(
                                audio=audio_file,
                                title=title[:64]
                            )

                    else:

                        # إذا كان MP4 نرسله كفيديو
                        if file_path.lower().endswith(
                            ".mp4"
                        ):

                            with open(
                                file_path,
                                "rb"
                            ) as video_file:

                                await query.message.reply_video(
                                    video=video_file,
                                    supports_streaming=True
                                )

                        else:

                            # WebM/MKV وغيرها
                            # نرسلها كملف بدل إعادة الترميز
                            with open(
                                file_path,
                                "rb"
                            ) as document:

                                await query.message.reply_document(
                                    document=document
                                )

                except Exception as telegram_error:

                    print(
                        "Telegram send error:",
                        repr(telegram_error)
                    )

                    # إذا فشل Telegram ننتقل إلى GoFile
                    await query.message.reply_text(
                        "⚠️ تعذر الإرسال المباشر.\n"
                        "جاري رفع الملف..."
                    )

                    link = await asyncio.to_thread(
                        upload_to_gofile,
                        file_path
                    )

                    if link:

                        await query.message.reply_text(
                            "✅ تم التحميل.\n\n"
                            f"🔗 رابط التحميل:\n{link}"
                        )

                    else:

                        await query.message.reply_text(
                            "❌ فشل رفع الملف إلى GoFile."
                        )

            # =====================================
            # أكبر من حد Telegram
            # =====================================

            else:

                await query.message.reply_text(
                    "📦 حجم الملف أكبر من حد Telegram.\n"
                    "جاري رفعه إلى GoFile..."
                )

                link = await asyncio.to_thread(
                    upload_to_gofile,
                    file_path
                )

                if link:

                    await query.message.reply_text(
                        "✅ تم التحميل بنجاح.\n\n"
                        f"📁 الحجم: "
                        f"{file_size / 1024 / 1024:.1f} MB\n\n"
                        f"🔗 رابط التحميل:\n{link}"
                    )

                else:

                    await query.message.reply_text(
                        "❌ تم تحميل الفيديو لكن فشل رفعه "
                        "إلى GoFile."
                    )

            # تنظيف
            cleanup_file(
                file_path
            )

            # حذف الرابط من الذاكرة
            context.user_data.pop(
                "url",
                None
            )

        except Exception as e:

            print(
                "DOWNLOAD ERROR:",
                repr(e)
            )

            error_text = str(e)

            # لا نرسل Logs ضخمة للمستخدم
            if len(error_text) > 1200:
                error_text = error_text[-1200:]

            await query.message.reply_text(
                "❌ تعذر تحميل الفيديو.\n\n"
                "الخطأ الأخير:\n"
                f"{error_text}"
            )


# =========================================================
# معالجة الأخطاء
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    print(
        "BOT ERROR:",
        repr(context.error)
    )


# =========================================================
# التشغيل
# =========================================================

def main():

    print("Starting Telegram Downloader Bot...")

    # Render Health Check
    start_health_server()

    # إنشاء التطبيق
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    # روابط / رسائل
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_url
        )
    )

    # الأزرار
    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # Errors
    application.add_error_handler(
        error_handler
    )

    print("Bot is running...")

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
