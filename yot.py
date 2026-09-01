import os
import re
import requests
import yt_dlp
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# جلب توكن البوت من متغيرات البيئة في Render
BOT_TOKEN = os.getenv("BOT_TOKEN")

def unshorten_url(url: str) -> str:
    """فك الروابط المختصرة مثل vt.tiktok.com للحصول على الرابط الأصلي"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.head(url, allow_redirects=True, timeout=10, headers=headers)
        return response.url
    except Exception:
        return url

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة البداية"""
    await update.message.reply_text("أهلاً بك! أرسل لي أي رابط (TikTok, YouTube, Instagram...) وسأقوم بتحميله لك مباشرة.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل وتحميل الفيديو"""
    raw_url = update.message.text.strip()
    
    # التحقق من أن النص يحتوي على رابط
    if not re.match(r'https?://', raw_url):
        await update.message.reply_text("يرجى إرسال رابط صحيح يبتدئ بـ http أو https.")
        return

    status_msg = await update.message.reply_text("جاري معالجة الرابط والتحميل... ⏳")
    
    # فك الرابط المختصر
    final_url = unshorten_url(raw_url)
    output_filename = f"video_{update.message.message_id}.mp4"

    # خيارات yt-dlp للتغلب على حظر TikTok والمواقع الأخرى
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_filename,
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.tiktok.com/',
        'nocheckcertificate': True,
    }

    try:
        # تحميل الفيديو إلى السيرفر
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([final_url])

        # إرسال الفيديو للمستخدم في تليجرام
        if os.path.exists(output_filename):
            await status_msg.edit_text("جاري رفع الفيديو إلى تليجرام... 📤")
            with open(output_filename, 'rb') as video_file:
                await update.message.reply_video(video=video_file, caption="تم التحميل بنجاح! 🚀")
            
            # حذف الملف المؤقت من السيرفر بعد الإرسال
            os.remove(output_filename)
            await status_msg.delete()
        else:
            await status_msg.edit_text("تعذر العثور على الملف المحمل، يرجى المحاولة مرة أخرى.")

    except Exception as e:
        # في حال حدوث خطأ
        if os.path.exists(output_filename):
            os.remove(output_filename)
        await status_msg.edit_text(f"حدث خطأ أثناء التحميل:\n`{str(e)}`", parse_mode="Markdown")

def main():
    if not BOT_TOKEN:
        print("خطأ: لم يتم ضبط BOT_TOKEN في Environment Variables!")
        return

    # بناء وتشغيل البوت
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("البوت يعمل الان...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
