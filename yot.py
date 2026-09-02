import os
import re
import logging
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp

# إعداد السجلات Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logger = logging.getLogger(__name__)

# جلب الرموز من متغيرات البيئة
TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")

# إنشاء تطبيق Flask والتليجرام
app = Flask(__name__)
application = Application.builder().token(TOKEN).build()


# دالة إعداد البوت والـ Webhook عند بدء التشغيل
async def setup_bot():
    await application.initialize()
    await application.start()
    if WEBHOOK_URL:
        url = f"{WEBHOOK_URL.rstrip('/')}/webhook"
        await application.bot.set_webhook(url=url)
        logger.info(f"✅ تم ربط الـ Webhook بنجاح على: {url}")

# تشغيل التهيئة فور تحميل الملف
asyncio.run(setup_bot())


@app.route('/')
def home():
    return "البوت شغال والسيرفر يعمل بنجاح!"


@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == "POST":
        try:
            update = Update.de_json(request.get_json(force=True), application.bot)
            # معالجة التحديث في دورة الأحداث Async
            asyncio.run(application.process_update(update))
        except Exception as e:
            logger.error(f"خطأ أثناء معالجة التحديث: {e}")
        return "ok", 200


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بك! أرسل لي أي رابط من يوتيوب، تيك توك، أو انستغرام وسأقوم بتحميله لك فوراً.")


def is_url(text):
    url_pattern = re.compile(
        r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    )
    return bool(url_pattern.match(text))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not is_url(url):
        await update.message.reply_text("الرجاء إرسال رابط صحيح لتحميل الفيديو.")
        return

    msg = await update.message.reply_text("جاري معالجة الرابط والتحميل... ⏳")
    filename = f"download_{update.message.message_id}.mp4"

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': filename,
        'quiet': True,
        'nocheckcertificate': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['ar', 'en'],
        'embedsubtitles': True,
        'geo_bypass': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'mweb', 'android'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }

    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = "cookies.txt"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        await msg.edit_text("تم التحميل بنجاح! جاري إرسال الفيديو... 📤")

        with open(filename, 'rb') as video_file:
            await update.message.reply_video(video=video_file, caption="تم التحميل بواسطة البوت 🎬")

        if os.path.exists(filename):
            os.remove(filename)
        await msg.delete()

    except Exception as e:
        logger.error(f"خطأ أثناء التحميل: {e}")
        await msg.edit_text(f"حدث خطأ أثناء التحميل:\n`{str(e)}`", parse_mode='Markdown')
        if os.path.exists(filename):
            os.remove(filename)


# تسجيل معالجات الأوامر
application.add_handler(CommandHandler("start", start_command))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
