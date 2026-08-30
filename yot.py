import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import yt_dlp

TOKEN = "8081564731:AAFazIC1PLGMdMF0yeMUCT915N2yOWci4L8"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بك! البوت يعمل الآن على سيرفر Render السحابي 🚀\n"
        "أرسل لي أي رابط وسأقوم بتحميله لك بسرعة عالية."
    )

async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not url.startswith("http://") and not url.startswith("https://"):
        await update.message.reply_text("من فضلك أرسل رابطاً صحيحاً يبدأ بـ http أو https")
        return

    context.user_data['url'] = url

    keyboard = [
        [
            InlineKeyboardButton("🎬 فيديو عالية (Best)", callback_data="format_best"),
            InlineKeyboardButton("🎬 720p", callback_data="format_720"),
        ],
        [
            InlineKeyboardButton("🎬 480p", callback_data="format_480"),
            InlineKeyboardButton("🎬 360p", callback_data="format_360"),
        ],
        [
            InlineKeyboardButton("🎵 صوت فقط MP3", callback_data="format_mp3")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("اختر الجودة أو الصيغة المطلوبة للتحميل:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    url = context.user_data.get('url')
    if not url:
        await query.edit_message_text("حدث خطأ، يرجى إعادة إرسال الرابط من جديد.")
        return

    choice = query.data
    await query.edit_message_text("جاري التحميل عبر السيرفر السحابي... ⏳")

    if choice == "format_best":
        fmt = "b/best"
    elif choice == "format_720":
        fmt = "bestvideo[height<=720]+bestaudio/best[height<=720]"
    elif choice == "format_480":
        fmt = "bestvideo[height<=480]+bestaudio/best[height<=480]"
    elif choice == "format_360":
        fmt = "bestvideo[height<=360]+bestaudio/best[height<=360]"
    elif choice == "format_mp3":
        fmt = "bestaudio/best"

    is_audio = (choice == "format_mp3")

    ydl_opts = {
        'format': fmt,
        'outtmpl': 'downloaded_media.%(ext)s',
        'quiet': True,
        'nocheckcertificate': True,
    }

    loop = asyncio.get_running_loop()

    def do_download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)

    try:
        file_path = await loop.run_in_executor(None, do_download)
        await query.edit_message_text("جاري إرسال الملف إليك... 📤")
        
        with open(file_path, 'rb') as media_file:
            if is_audio:
                await query.message.reply_audio(audio=media_file)
            else:
                await query.message.reply_video(video=media_file)
            
        if os.path.exists(file_path):
            os.remove(file_path)
            
        await query.delete_message()

    except Exception as e:
        await query.edit_message_text(f"حدث خطأ أثناء التحميل:\n`{str(e)}`", parse_mode="Markdown")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).read_timeout(120).write_timeout(120).connect_timeout(60).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("البوت يعمل أونلاين على السيرفر...")
    app.run_polling()

