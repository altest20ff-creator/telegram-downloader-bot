import os
import threading
import requests
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp

# --- 1. إنشاء خادم Flask لإبقاء الخدمة نشطة على Render ---
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot is running 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host="0.0.0.0", port=port)

# --- 2. إعدادات البوت الرئيسية ---
TOKEN = os.getenv("BOT_TOKEN", "8081564731:AAFazIC1PLGMdMF0yeMUCT915N2yOWci4L8")
CHANNEL_USERNAME = "@kingdeveloper2004"
CHANNEL_URL = "https://t.me/kingdeveloper2004"

def upload_to_gofile(file_path):
    """رفع الملفات الكبيرة إلى GoFile"""
    try:
        server_resp = requests.get("https://api.gofile.io/getServer").json()
        if server_resp.get("status") == "ok":
            server = server_resp["data"]["server"]
            upload_url = f"https://{server}.gofile.io/uploadFile"
            
            with open(file_path, 'rb') as f:
                response = requests.post(upload_url, files={'file': f}).json()
                
            if response.get("status") == "ok":
                return response["data"]["downloadPage"]
    except Exception as e:
        print(f"GoFile Upload Error: {e}")
    return None

async def check_subscription(user_id, context):
    """فحص اشتراك المستخدم في القناة"""
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        print(f"خطأ في التحقق من الاشتراك: {e}")
        return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_subscribed = await check_subscription(user_id, context)

    if not is_subscribed:
        keyboard = [
            [InlineKeyboardButton("📢 اشترك في القناة هنا", url=CHANNEL_URL)],
            [InlineKeyboardButton("✅ تحقّق من الاشتراك", callback_data='check_sub')]
        ]
        await update.message.reply_text(
            "⚠️ **عذراً! يجب عليك الاشتراك في قناتنا أولاً لاستخدام البوت.**\n\nاشترك ثم اضغط على زر التحقّق بالأسفل 👇",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    keyboard = [[InlineKeyboardButton("📢 قناتنا الرسمية", url=CHANNEL_URL)]]
    await update.message.reply_text(
        "أهلاً بك! أرسل لي رابط أي فيديو وسأقوم بتحميله لك فوراً مع الترجمة والجودة المطلوبة.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_subscribed = await check_subscription(user_id, context)

    if not is_subscribed:
        keyboard = [
            [InlineKeyboardButton("📢 اشترك في القناة هنا", url=CHANNEL_URL)],
            [InlineKeyboardButton("✅ تحقّق من الاشتراك", callback_data='check_sub')]
        ]
        await update.message.reply_text(
            "⚠️ **يجب عليك الاشتراك في القناة أولاً لاستخدام البوت.**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("من فضلك أرسل رابطاً صحيحاً يبدأ بـ http أو https.")
        return

    context.user_data['url'] = url
    keyboard = [
        [InlineKeyboardButton("🎬 أعلى جودة متاحة", callback_data='best')],
        [InlineKeyboardButton("📹 1080p (FHD)", callback_data='1080p'), InlineKeyboardButton("📹 720p (HD)", callback_data='720p')],
        [InlineKeyboardButton("📱 480p (SD)", callback_data='480p'), InlineKeyboardButton("📱 360p (Low)", callback_data='360p')],
        [InlineKeyboardButton("🎵 صوت فقط (MP3)", callback_data='audio')],
        [InlineKeyboardButton("📢 قناتنا على تليجرام", url=CHANNEL_URL)]
    ]
    await update.message.reply_text("اختر الجودة أو الصيغة المطلوبة:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'check_sub':
        user_id = query.from_user.id
        if await check_subscription(user_id, context):
            await query.edit_message_text("✅ تم التحقق من اشتراكك بنجاح! أرسل رابط الفيديو الآن.")
        else:
            await query.answer("❌ لم تشترك في القناة بعد! يرجى الاشتراك أولاً.", show_alert=True)
        return

    url = context.user_data.get('url')
    if not url:
        await query.edit_message_text("حدث خطأ أو انتهت الجلسة، يرجى إرسال الرابط من جديد.")
        return

    choice = query.data
    await query.edit_message_text("⏳ جاري التحميل، معالجة الجودة وتدميج الترجمة إن وجدت...")

    filename = "downloaded_media.mp4" if choice != 'audio' else "downloaded_media.mp3"
    
    if choice == 'best':
        fmt = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    elif choice in ['1080p', '720p', '480p', '360p']:
        height = choice.replace('p', '')
        fmt = f'bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={height}][ext=mp4]/best'
    else:
        fmt = 'bestaudio/best'

    ydl_opts = {
        'format': fmt,
        'outtmpl': filename,
        'quiet': True,
        'nocheckcertificate': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['ar', 'en'],
        'embedsubtitles': True,
        'geo_bypass': True,
        'user_agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web_embedded'],
                'skip': ['hls', 'dash']
            },
            'tiktok': {
                'app_version': '30.0.0'
            }
        }
    }

    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = "cookies.txt"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not os.path.exists(filename):
            await query.edit_message_text("❌ تعذر تحميل الملف، تأكد من صحة الرابط أو جرب جودة أخرى.")
            return

        file_size = os.path.getsize(filename) / (1024 * 1024)
        channel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📢 اشترك في قناتنا", url=CHANNEL_URL)]])

        if file_size < 48:
            await query.edit_message_text("⬆️ جاري رفع الملف إلى تليجرام...")
            with open(filename, 'rb') as f:
                if choice == 'audio':
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=f,
                        caption="تم التحميل بواسطة البوت 🎬\n📢 " + CHANNEL_URL,
                        reply_markup=channel_keyboard
                    )
                else:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=f,
                        caption="تم التحميل بواسطة البوت 🎬\n📢 " + CHANNEL_URL,
                        reply_markup=channel_keyboard
                    )
            await query.delete_message()
        else:
            await query.edit_message_text("📦 حجم الملف كبير جداً (>48MB)، جاري رفعه على سيرفر خارجي...")
            download_link = upload_to_gofile(filename)
            if download_link:
                await query.edit_message_text(
                    f"✅ **تم التحميل بنجاح!**\n\n📏 **الحجم:** `{file_size:.1f} MB`\n\n🔗 **الرابط المباشر:**\n{download_link}\n\n📢 {CHANNEL_URL}",
                    parse_mode="Markdown",
                    reply_markup=channel_keyboard
                )
            else:
                await query.edit_message_text("❌ تعذر رفع الملف الكبير، يرجى المحاولة لاحقاً.")

    except Exception as e:
        await query.edit_message_text(f"حدث خطأ أثناء التحميل:\n`{str(e)}`", parse_mode="Markdown")
    finally:
        if os.path.exists(filename):
            os.remove(filename)

# --- 3. تشغيل الـ Flask Server والبوت معاً ---
if __name__ == '__main__':
    # تشغيل سيرفر Flask في Thread منفصل
    threading.Thread(target=run_flask, daemon=True).start()

    # تشغيل بوت تليجرام
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_click))
    print("البوت وسيرفر الويب يعملان الآن بنجاح...")
    app.run_polling()
