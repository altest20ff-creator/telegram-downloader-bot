import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import yt_dlp

# توكن البوت الخاص بك
TOKEN = "8081564731:AAFazIC1PLGMdMF0yeMUCT915N2yOWci4L8"

def upload_to_gofile(file_path):
    """رفع الملف إلى GoFile والحصول على رابط مباشر مجاني"""
    try:
        # الحصول على أفضل سيرفر للرفع
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بك! أرسل لي رابط أي فيديو من (يوتيوب، إنستغرام، تيك توك، فيسبوك) وسأقوم بتحميله لك فوراً.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("من فضلك أرسل رابطاً صحيحاً.")
        return

    context.user_data['url'] = url
    keyboard = [
        [InlineKeyboardButton("📹 فيديو (أعلى جودة)", callback_data='video')],
        [InlineKeyboardButton("🎵 صوت فقط (MP3)", callback_data='audio')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("اختر صيغة التحميل المطلوبة:", reply_markup=reply_markup)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    url = context.user_data.get('url')
    if not url:
        await query.edit_message_text("حدث خطأ، يرجى إرسال الرابط من جديد.")
        return

    choice = query.data
    await query.edit_message_text("⏳ جاري التحميل والمعالجة، يرجى الانتظار...")

    filename = "downloaded_media.mp4" if choice == 'video' else "downloaded_media.mp3"
    fmt = 'bestvideo+bestaudio/best' if choice == 'video' else 'bestaudio/best'

    ydl_opts = {
        'format': fmt,
        'outtmpl': filename,
        'quiet': True,
        'nocheckcertificate': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        file_size = os.path.getsize(filename) / (1024 * 1024)  # الحجم بالميجابايت

        # إذا كان الحجم أقل من 48 ميجابايت يرسله مباشرة على تليجرام
        if file_size < 48:
            await query.edit_message_text("⬆️ جاري رفع الملف إلى تليجرام...")
            with open(filename, 'rb') as f:
                if choice == 'video':
                    await context.bot.send_video(chat_id=query.message.chat_id, video=f)
                else:
                    await context.bot.send_audio(chat_id=query.message.chat_id, audio=f)
            await query.delete_message()
        else:
            # إذا كان الملف كبيراً يرفعه إلى GoFile مجاناً
            await query.edit_message_text("📦 حجم الملف كبير جداً لتليجرام، جاري رفعه على سيرفر خارجي مجاني...")
            download_link = upload_to_gofile(filename)
            
            if download_link:
                await query.edit_message_text(
                    f"✅ **تم التحميل بنجاح!**\n\n"
                    f"حجم الملف: `{file_size:.1f} MB`\n"
                    f"🔗 **رابط التحميل المباشر:**\n{download_link}",
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text("❌ تعذر رفع الملف الكبير، يرجى المحاولة لاحقاً.")

    except Exception as e:
        await query.edit_message_text(f"حدث خطأ أثناء التحميل:\n`{str(e)}`", parse_mode="Markdown")
    finally:
        if os.path.exists(filename):
            os.remove(filename)

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_click))
    print("البوت يعمل الآن بدون أخطاء...")
    app.run_polling()

