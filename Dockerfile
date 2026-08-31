FROM python:3.10-slim

# تثبيت مكتبة ffmpeg للتحميل والتعامل مع الفيديوهات
RUN apt-get update && apt-get install -y ffmpeg

WORKDIR /app

# نسخ وتثبيت مكتبات بايثون
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات المشروع
COPY . .

# أمر تشغيل الملف الرئيسي للبوت
CMD ["python", "app.py"]
