import os
import re
import shutil
import asyncio
import tempfile
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

# =========================
# الإعدادات
# =========================
# جلب التوكن من متغيرات البيئة تلقائياً لعدم تسريبه
TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_USERNAME = "@kingdeveloper2004"
CHANNEL_URL = "https://t.me/kingdeveloper2004"

# حدود التليجرام
TELEGRAM_LIMIT_MB = 49

# قفل لمنع التحميل المتزامن لحماية الـ RAM (512MB limit)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(1)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("downloader")


# =========================
# أدوات مساعدة
# =========================
def clean_error(exc):
    s = str(exc)
    if "The page needs to be reloaded" in s:
        return "YouTube رفض الجلسة الحالية. تأكد من تحديث ملف cookies.txt."
    return s[-1500:]


def upload_to_gofile(file_path):
    """رفع الملف مباشرة إلى GoFile."""
    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                "https://upload.gofile.io/uploadfile",
                files={"file": f},
                timeout=1800,
            )
        data = r.json()

        if data.get("status") == "ok":
            d = data.get("data", {})
            return d.get("downloadPage") or d.get("directLink")

        log.error("GoFile response: %s", data)
    except Exception:
        log.exception("GoFile upload failed")
    return None


async def check_subscription(user_id, context):
    try:
        member = await context.bot.get_chat_member(
            chat_id=CHANNEL_USERNAME,
            user_id=user_id,
        )
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        log.warning("Subscription check failed: %s", e)
        return True


def build_formats(choice):
    if choice == "audio":
        return ["bestaudio/best"]

    if choice == "best":
        return ["bestvideo+bestaudio/best"]

    height = choice.replace("p", "")
    return [
        f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
        f"best[height<={height}]",
        "bestvideo+bestaudio/best",
        "best",
    ]


def make_ydl_opts(outtmpl, choice):
    opts = {
        "outtmpl": outtmpl,
        "format": build_formats(choice)[0],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "continuedl": True,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 5,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 1,
        "overwrites": True,
        "merge_output_format": "mp4",
        "sleep_interval": 1,
        "max_sleep_interval": 2,
    }

    # قراءة cookies.txt إذا كان موجوداً في المسار الرئيسي
    cookie_path = Path("cookies.txt")
    if cookie_path.exists() and cookie_path.stat().st_size > 0:
        opts["cookiefile"] = str(cookie_path)
        log.info("تم العثور على ملف cookies.txt واستخدامه.")

    return opts


def try_download(url, workdir, choice):
    """تحميل الفيديو مع مراعاة القيود وتجنب فرض player_client متصلب."""
    errors = []

    for fmt in build_formats(choice):
        filename = Path(workdir) / "media"
        outtmpl = str(filename) + ".%(ext)s"

        opts = make_ydl_opts(outtmpl, choice)
        opts["format"] = fmt

        try:
            log.info("Trying format=%s", fmt)

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

            candidates = [
                p for p in Path(workdir).glob("media.*")
                if p.is_file() and p.stat().st_size > 0
            ]

            if candidates:
                candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
                return candidates[0], errors

        except Exception as e:
            msg = clean_error(e)
            errors.append(f"{fmt} | {msg}")
            log.warning("Download attempt failed: %s", msg)

            for p in Path(workdir).glob("media.*"):
                try:
                    if p.is_file():
                        p.unlink()
                except Exception:
                    pass

    return None, errors


def convert_audio_to_mp3(source, destination):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg غير مثبت على السيرفر.")

    import subprocess

    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i", str(source),
            "-vn",
            "-codec:a", "libmp3lame",
            "-q:a", "4",
            str(destination),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=1800,
    )


def convert_video_to_mp4_if_needed(source, destination):
    """تجنب إعادة الترميز واستهلاك RAM قدر الإمكان."""
    if source.suffix.lower() == ".mp4":
        shutil.move(str(source), str(destination))
        return

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg غير مثبت على السيرفر.")

    import subprocess

    # المحاولة الأولى: نسخ Stream فقط (بدون إجهاد CPU/RAM)
    p = subprocess.run(
        [
            ffmpeg, "-y",
            "-i", str(source),
            "-c", "copy",
            str(destination),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=1800,
    )

    if p.returncode != 0:
        # إعادة ترميز خفيف جداً مناسب لحدود Render (خيارات تستهلك ذاكرة أقل)
        subprocess.run(
            [
                ffmpeg, "-y",
                "-i", str(source),
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "28",
                "-c:a", "aac",
                "-b:a", "128k",
                str(destination),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=3600,
        )


# =========================
# Telegram Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok = await check_subscription(update.effective_user.id, context)

    if not ok:
        kb = [
            [InlineKeyboardButton("📢 اشترك في القناة", url=CHANNEL_URL)],
            [InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_sub")],
        ]
        await update.message.reply_text(
            "⚠️ يجب الاشتراك في القناة أولاً.",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    await update.message.reply_text(
        "أرسل رابط الفيديو من YouTube وسأقوم بتحميله لك."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    if not await check_subscription(update.effective_user.id, context):
        kb = [
            [InlineKeyboardButton("📢 اشترك في القناة", url=CHANNEL_URL)],
            [InlineKeyboardButton("✅ تحقق", callback_data="check_sub")],
        ]
        await update.message.reply_text(
            "⚠️ اشترك في القناة أولاً.",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    url = update.message.text.strip()

    if not re.match(r"^https?://", url, re.I):
        await update.message.reply_text("❌ أرسل رابطاً يبدأ بـ http أو https.")
        return

    context.user_data["url"] = url

    kb = [
        [InlineKeyboardButton("🎬 أعلى جودة", callback_data="best")],
        [
            InlineKeyboardButton("1080p", callback_data="1080p"),
            InlineKeyboardButton("720p", callback_data="720p"),
        ],
        [
            InlineKeyboardButton("480p", callback_data="480p"),
            InlineKeyboardButton("360p", callback_data="360p"),
        ],
        [InlineKeyboardButton("🎵 MP3", callback_data="audio")],
    ]

    await update.message.reply_text(
        "اختر الجودة المطلوبة:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "check_sub":
        if await check_subscription(query.from_user.id, context):
            await query.edit_message_text("✅ تم التحقق. أرسل رابط الفيديو.")
        else:
            await query.answer(
                "❌ لم يتم العثور على الاشتراك.",
                show_alert=True,
            )
        return

    url = context.user_data.get("url")
    if not url:
        await query.edit_message_text("❌ انتهت الجلسة. أرسل الرابط من جديد.")
        return

    choice = query.data

    # استخدام السيمفور لضمان تنفيذ عملية تحميل واحدة فقط في نفس الوقت على السيرفر
    if DOWNLOAD_SEMAPHORE.locked():
        await query.edit_message_text("⏳ يوجد عملية تحميل أخرى قيد التنفيذ، يرجى الانتظار قليلاً...")

    async with DOWNLOAD_SEMAPHORE:
        await query.edit_message_text("⏳ جاري التحميل والترتيب...")

        workdir = tempfile.mkdtemp(prefix="ytbot_")

        try:
            source, errors = await asyncio.to_thread(
                try_download,
                url,
                workdir,
                choice,
            )

            if not source:
                last = errors[-1] if errors else "لا توجد تفاصيل."
                await query.edit_message_text(
                    "❌ تعذر تحميل هذا الفيديو.\n\n"
                    f"السبب:\n{last[:1200]}"
                )
                return

            final_file = Path(workdir) / (
                "audio.mp3" if choice == "audio" else "video.mp4"
            )

            if choice == "audio":
                await query.edit_message_text("🎵 جاري تجهيز ملف MP3...")
                await asyncio.to_thread(
                    convert_audio_to_mp3,
                    source,
                    final_file,
                )
            else:
                await query.edit_message_text("⚙️ جاري تجهيز الفيديو...")
                await asyncio.to_thread(
                    convert_video_to_mp4_if_needed,
                    source,
                    final_file,
                )

            if not final_file.exists():
                raise RuntimeError("لم يتم إنشاء الملف النهائي.")

            size_mb = final_file.stat().st_size / 1024 / 1024

            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("📢 قناتنا", url=CHANNEL_URL)]]
            )

            if size_mb <= TELEGRAM_LIMIT_MB:
                await query.edit_message_text("⬆️ جاري إرسال الملف إلى Telegram...")

                with open(final_file, "rb") as f:
                    if choice == "audio":
                        await context.bot.send_audio(
                            chat_id=query.message.chat_id,
                            audio=f,
                            caption="✅ تم التحميل\n📢 " + CHANNEL_URL,
                            reply_markup=kb,
                        )
                    else:
                        await context.bot.send_video(
                            chat_id=query.message.chat_id,
                            video=f,
                            caption="✅ تم التحميل\n📢 " + CHANNEL_URL,
                            supports_streaming=True,
                            reply_markup=kb,
                        )

                await query.delete_message()

            else:
                await query.edit_message_text(
                    f"📦 حجم الملف {size_mb:.1f} MB، أكبر من حد Telegram.\n"
                    "⬆️ جاري رفعه إلى GoFile..."
                )

                link = await asyncio.to_thread(upload_to_gofile, str(final_file))

                if link:
                    await query.edit_message_text(
                        f"✅ تم التحميل بنجاح!\n\n"
                        f"📏 الحجم: {size_mb:.1f} MB\n\n"
                        f"🔗 الرابط:\n{link}\n\n"
                        f"📢 {CHANNEL_URL}",
                        reply_markup=kb,
                    )
                else:
                    await query.edit_message_text(
                        "❌ تم تحميل الفيديو لكن فشل رفعه إلى GoFile.\n"
                        "أعد المحاولة لاحقاً."
                    )

        except Exception as e:
            log.exception("Processing error")
            await query.edit_message_text(
                "❌ حدث خطأ أثناء تجهيز الملف:\n\n"
                + clean_error(e)
            )

        finally:
            shutil.rmtree(workdir, ignore_errors=True)


# =========================
# تشغيل
# =========================
def main():
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN غير موجود. تأكد من إضافته إلى متغيرات البيئة (Environment Variables)."
        )

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    app.add_handler(CallbackQueryHandler(button_click))

    log.info("Bot started successfully.")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
