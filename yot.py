import os
import re
import shutil
import tempfile
import asyncio
import logging
from pathlib import Path

import requests
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================================================
# الإعدادات
# =========================================================
# ضع التوكن في متغير بيئة، ولا تضعه داخل GitHub.
TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_USERNAME = "@kingdeveloper2004"
CHANNEL_URL = "https://t.me/kingdeveloper2004"

# أقل من حد Telegram بقليل لتجنب أخطاء الحجم.
TELEGRAM_LIMIT_MB = 49

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


# =========================================================
# GoFile - واجهة الرفع الحالية
# =========================================================
def upload_to_gofile(file_path: str) -> str | None:
    """يرفع الملف إلى GoFile ويعيد رابط صفحة التحميل."""
    try:
        # الواجهة العالمية الحالية لا تحتاج getServer.
        with open(file_path, "rb") as f:
            r = requests.post(
                "https://upload.gofile.io/uploadfile",
                files={"file": (Path(file_path).name, f)},
                timeout=1800,
            )

        r.raise_for_status()
        data = r.json()

        if data.get("status") == "ok":
            info = data.get("data", {})
            return info.get("downloadPage") or info.get("directLink")

        log.error("GoFile response: %s", data)

    except Exception:
        log.exception("GoFile upload failed")

    return None


# =========================================================
# الاشتراك
# =========================================================
async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(
            chat_id=CHANNEL_USERNAME,
            user_id=user_id,
        )
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        # لا تعتبر الخطأ اشتراكاً صحيحاً.
        log.error("Subscription check failed: %s", e)
        return False


def subscription_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 اشترك في القناة", url=CHANNEL_URL)],
        [InlineKeyboardButton("✅ تحقّق من الاشتراك", callback_data="check_sub")],
    ])


# =========================================================
# تنظيف اسم الملف
# =========================================================
def safe_title(title: str) -> str:
    title = re.sub(r'[\\/:*?"<>|]+', "_", title or "video")
    return title[:80].strip() or "video"


# =========================================================
# اختيار الصيغة
# =========================================================
def format_for_choice(choice: str) -> str:
    # مهم جداً:
    # لا نستخدم ext=mp4 أو player_client=android/ios/mweb بشكل إجباري.
    # هذه كانت من أسباب "Requested format is not available".

    if choice == "audio":
        return "bestaudio/best"

    if choice == "best":
        return "bestvideo*+bestaudio/best"

    heights = {
        "1080p": 1080,
        "720p": 720,
        "480p": 480,
        "360p": 360,
    }

    h = heights.get(choice)
    if h:
        # إذا لم توجد صيغة منفصلة مناسبة، جرّب صيغة مدمجة بنفس الحد.
        return (
            f"bestvideo*[height<={h}]+bestaudio/"
            f"best[height<={h}]/best"
        )

    return "bestvideo*+bestaudio/best"


# =========================================================
# تنزيل yt-dlp
# =========================================================
def download_media(url: str, choice: str, workdir: str) -> str:
    """
    تنزيل الفيديو.
    لا نفرض YouTube player clients لأن YouTube حالياً يفرض PO Tokens
    على بعض العملاء/الصيغ.
    """
    output_template = os.path.join(workdir, "%(title).80s.%(ext)s")

    opts = {
        "format": format_for_choice(choice),
        "outtmpl": output_template,
        "noplaylist": True,

        # إعادة المحاولة
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "extractor_retries": 5,

        # الشبكة
        "socket_timeout": 30,
        "http_chunk_size": 10 * 1024 * 1024,

        # لا تجعل yt-dlp يختار عملاء Android/iOS بالقوة.
        # لا نضع extractor_args هنا.

        # السماح بالكوكيز فقط إذا كان الملف موجوداً.
        # لا تحتاجه معظم الفيديوهات العامة.
        "cookiefile": "cookies.txt" if os.path.exists("cookies.txt") else None,

        "quiet": True,
        "no_warnings": False,
        "ignoreerrors": False,

        # ترجمات إن وجدت
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["ar", "en"],
        "subtitlesformat": "vtt",

        # ترتيب أفضل نتيجة متاحة
        "merge_output_format": "mp4",

        # عدم تنزيل قوائم التشغيل
        "overwrites": True,
    }

    # نحذف cookiefile إذا لم يكن موجوداً حتى لا يسبب قيمة None مشكلة.
    if opts["cookiefile"] is None:
        opts.pop("cookiefile")

    # للصوت: نحول النتيجة إلى MP3 عبر ffmpeg.
    if choice == "audio":
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
        opts["merge_output_format"] = "mp3"

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    files = [
        p for p in Path(workdir).iterdir()
        if p.is_file()
        and not p.name.endswith((".part", ".ytdl"))
        and not p.name.endswith(".vtt")
        and not p.name.endswith(".srt")
    ]

    if not files:
        raise RuntimeError("yt-dlp انتهى بدون إنشاء ملف.")

    # اختر أكبر ملف فعلياً.
    return str(max(files, key=lambda p: p.stat().st_size))


# =========================================================
# أزرار البداية
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update.effective_user.id, context):
        await update.message.reply_text(
            "⚠️ يجب الاشتراك في القناة أولاً.",
            reply_markup=subscription_keyboard(),
        )
        return

    await update.message.reply_text(
        "أهلاً بك 👋\n"
        "أرسل رابط الفيديو وسأعطيك خيارات الجودة."
    )


# =========================================================
# استقبال الرابط
# =========================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update.effective_user.id, context):
        await update.message.reply_text(
            "⚠️ يجب الاشتراك في القناة أولاً.",
            reply_markup=subscription_keyboard(),
        )
        return

    url = (update.message.text or "").strip()

    if not re.match(r"^https?://", url, re.I):
        await update.message.reply_text(
            "❌ أرسل رابطاً يبدأ بـ http:// أو https://"
        )
        return

    context.user_data["url"] = url

    keyboard = [
        [InlineKeyboardButton("🎬 أعلى جودة متاحة", callback_data="best")],
        [
            InlineKeyboardButton("📹 1080p", callback_data="1080p"),
            InlineKeyboardButton("📹 720p", callback_data="720p"),
        ],
        [
            InlineKeyboardButton("📱 480p", callback_data="480p"),
            InlineKeyboardButton("📱 360p", callback_data="360p"),
        ],
        [InlineKeyboardButton("🎵 MP3", callback_data="audio")],
    ]

    await update.message.reply_text(
        "اختر الجودة:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =========================================================
# معالجة الزر
# =========================================================
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "check_sub":
        if await check_subscription(query.from_user.id, context):
            await query.edit_message_text(
                "✅ تم التحقق.\nأرسل رابط الفيديو الآن."
            )
        else:
            await query.answer(
                "❌ لم يتم العثور على اشتراكك. اشترك أولاً.",
                show_alert=True,
            )
        return

    url = context.user_data.get("url")
    if not url:
        await query.edit_message_text(
            "❌ انتهت الجلسة. أرسل الرابط من جديد."
        )
        return

    choice = query.data
    await query.edit_message_text(
        "⏳ جاري التحميل...\n"
        "إذا كانت الصيغة المطلوبة غير موجودة، سيستخدم البوت أفضل صيغة متاحة تلقائياً."
    )

    workdir = tempfile.mkdtemp(prefix="ytbot_")

    try:
        # yt-dlp + ffmpeg عمليات blocking، لذلك نشغلها خارج event loop.
        file_path = await asyncio.to_thread(
            download_media, url, choice, workdir
        )

        size_mb = os.path.getsize(file_path) / (1024 * 1024)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 قناتنا", url=CHANNEL_URL)]
        ])

        if size_mb <= TELEGRAM_LIMIT_MB:
            await query.edit_message_text("⬆️ جاري إرسال الملف إلى Telegram...")

            with open(file_path, "rb") as f:
                if choice == "audio":
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=f,
                        caption="✅ تم التحميل\n📢 " + CHANNEL_URL,
                        reply_markup=keyboard,
                        read_timeout=300,
                        write_timeout=300,
                        connect_timeout=60,
                    )
                else:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=f,
                        caption="✅ تم التحميل\n📢 " + CHANNEL_URL,
                        reply_markup=keyboard,
                        supports_streaming=True,
                        read_timeout=300,
                        write_timeout=300,
                        connect_timeout=60,
                    )

            await query.delete_message()

        else:
            await query.edit_message_text(
                f"📦 حجم الملف {size_mb:.1f} MB، أكبر من حد Telegram.\n"
                "⬆️ جاري رفعه إلى GoFile..."
            )

            link = await asyncio.to_thread(upload_to_gofile, file_path)

            if not link:
                raise RuntimeError(
                    "تم التنزيل بنجاح لكن رفع الملف الكبير إلى GoFile فشل."
                )

            await query.edit_message_text(
                f"✅ تم التحميل بنجاح!\n\n"
                f"📏 الحجم: {size_mb:.1f} MB\n\n"
                f"🔗 رابط التحميل:\n{link}\n\n"
                f"📢 {CHANNEL_URL}",
                reply_markup=keyboard,
            )

    except Exception as e:
        log.exception("Download job failed")

        # رسالة مفيدة للمستخدم بدلاً من traceback طويل.
        msg = str(e)

        if "Requested format is not available" in msg:
            msg = (
                "YouTube لم يعرض الصيغة المطلوبة لهذا الفيديو. "
                "الكود الجديد يحاول الصيغة الاحتياطية تلقائياً، "
                "لكن هذا الفيديو قد يحتاج طريقة وصول مختلفة."
            )
        elif "Sign in" in msg or "LOGIN_REQUIRED" in msg:
            msg = "هذا الفيديو يحتاج تسجيل دخول/كوكيز صالحة."
        elif "ffmpeg" in msg.lower():
            msg = "FFmpeg غير مثبت أو غير موجود في PATH."

        await query.edit_message_text(
            "❌ تعذر إكمال العملية.\n\n"
            f"{msg[:1200]}"
        )

    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# =========================================================
# التشغيل
# =========================================================
def main():
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN غير موجود. في Termux نفّذ:\n"
            "export BOT_TOKEN='ضع_التوكن_الجديد_هنا'"
        )

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    app.add_handler(CallbackQueryHandler(button_click))

    print("✅ البوت يعمل...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
