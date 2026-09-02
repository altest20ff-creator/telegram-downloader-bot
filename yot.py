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
TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_USERNAME = "@kingdeveloper2004"
CHANNEL_URL = "https://t.me/kingdeveloper2004"

# اتركه أقل من حد Telegram بقليل
TELEGRAM_LIMIT_MB = 49
MAX_RETRIES = 3

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("downloader")


# =========================
# أدوات
# =========================
def human_size(n):
    return f"{n / 1024 / 1024:.1f} MB"


def clean_error(exc):
    s = str(exc)
    if "The page needs to be reloaded" in s:
        return (
            "YouTube رفض جلسة الاستخراج الحالية. "
            "تمت محاولة طرق أخرى تلقائياً. إذا استمر الخطأ فهذا من حماية YouTube."
        )
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
        # إذا تعذر فحص القناة لا نمنع المستخدم
        return True


def build_formats(choice):
    """
    لا نطلب ext=mp4 بشكل إجباري.
    هذا مهم لأن الصيغ المتاحة تختلف من فيديو لآخر.
    """
    if choice == "audio":
        return [
            "bestaudio/best",
        ]

    if choice == "best":
        return [
            "bestvideo*+bestaudio/best",
            "best",
        ]

    height = choice.replace("p", "")
    return [
        f"bestvideo*[height<={height}]+bestaudio/best[height<={height}]",
        f"best[height<={height}]",
        "bestvideo*+bestaudio/best",
        "best",
    ]


def extractor_variants():
    """
    لا نثبت android/ios/mweb فقط.
    نحاول عملاء أكثر ملاءمة للوضع الحالي في YouTube.
    """
    return [
        None,  # اترك yt-dlp يختار افتراضياً
        "web_embedded",
        "android_vr",
        "web_safari",
    ]


def make_ydl_opts(outtmpl, choice, client):
    opts = {
        "outtmpl": outtmpl,
        "format": build_formats(choice)[0],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,

        # مهم للسيرفر الضعيف: لا نحمل الفيديو كله في RAM
        "continuedl": True,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 5,
        "extractor_retries": 3,

        "socket_timeout": 30,
        "concurrent_fragment_downloads": 1,

        # يفضل ملفاً مؤقتاً ثم نعيد التسمية
        "overwrites": True,

        # لا تستخدم cookies.txt تلقائياً؛ الكوكيز القديمة قد تسبب
        # "The page needs to be reloaded"
        "cookiefile": None,

        # ترجمات اختيارية
        "writesubtitles": False,
        "writeautomaticsub": False,
        "embedsubtitles": False,

        # لا نمنع yt-dlp من اختيار الصيغ المتاحة
        "merge_output_format": "mp4",

        # تهدئة بسيطة لتقليل ضغط YouTube
        "sleep_interval": 1,
        "max_sleep_interval": 2,
    }

    if client:
        opts["extractor_args"] = {
            "youtube": {
                "player_client": [client],
            }
        }

    # yt-dlp الحديث يحتاج JS runtime في بعض حالات YouTube.
    # إذا كان Deno مثبتاً سيستفيد منه تلقائياً.
    return opts


def try_download(url, workdir, choice):
    """
    يجرب عدة صيغ وعملاء.
    يرجع الملف عند نجاح أي محاولة.
    """
    errors = []

    for client in extractor_variants():
        for fmt in build_formats(choice):
            filename = Path(workdir) / "media"
            outtmpl = str(filename) + ".%(ext)s"

            opts = make_ydl_opts(outtmpl, choice, client)
            opts["format"] = fmt

            try:
                log.info("Trying client=%s format=%s", client or "default", fmt)

                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])

                candidates = [
                    p for p in Path(workdir).glob("media.*")
                    if p.is_file() and p.stat().st_size > 0
                ]

                if candidates:
                    # اختر أكبر ملف، وغالباً هو الناتج النهائي
                    candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
                    return candidates[0], errors

            except Exception as e:
                msg = clean_error(e)
                errors.append(f"{client or 'default'} | {fmt} | {msg}")
                log.warning("Download attempt failed: %s", msg)

                # نظف أي ملفات جزئية قبل المحاولة التالية
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
    """
    نحاول عدم إعادة ترميز الفيديو حتى لا نستهلك RAM/CPU.
    إذا كان الملف MP4 ننقله فقط.
    """
    if source.suffix.lower() == ".mp4":
        shutil.move(str(source), str(destination))
        return

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg غير مثبت على السيرفر.")

    import subprocess

    # إعادة تغليف بدون إعادة ترميز عندما يكون ذلك ممكناً
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
        # آخر حل: إعادة ترميز خفيف
        subprocess.run(
            [
                ffmpeg, "-y",
                "-i", str(source),
                "-c:v", "libx264",
                "-preset", "veryfast",
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
# Telegram
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
        "أرسل رابط الفيديو من YouTube وسأحاول تحميله بأكثر من طريقة تلقائياً."
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
        "اختر الجودة. إذا كانت الصيغة المطلوبة غير متاحة سأجرب صيغة بديلة تلقائياً.",
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

    await query.edit_message_text(
        "⏳ جاري التحميل...\n"
        "سأجرب أكثر من طريقة تلقائياً إذا رفضت YouTube الطريقة الأولى."
    )

    workdir = tempfile.mkdtemp(prefix="ytbot_")

    try:
        source, errors = await asyncio.to_thread(
            try_download,
            url,
            workdir,
            choice,
        )

        if not source:
            # لا نرسل كل سجل الأخطاء للمستخدم
            last = errors[-1] if errors else "لا توجد تفاصيل."
            await query.edit_message_text(
                "❌ تعذر تحميل هذا الفيديو بعد تجربة عدة طرق.\n\n"
                f"السبب الأخير:\n{last[:1200]}"
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
            # إذا لم يكن MP4، نحاول جعله MP4
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
            "BOT_TOKEN غير موجود. ضع التوكن الجديد في متغير البيئة BOT_TOKEN."
        )

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    app.add_handler(CallbackQueryHandler(button_click))

    log.info("Bot started.")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
