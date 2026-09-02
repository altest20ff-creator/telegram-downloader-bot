# استخدام نسخة Python مستقرة وخفيفة
FROM python:3.11-slim

# تثبيت FFmpeg وتحديث حزم النظام
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# تحديد مجلد العمل داخل الـ Container
WORKDIR /app

# نسخ ملف المكتبات وتثبيتها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ جميع ملفات المشروع (بما فيها الكود و cookies.txt)
COPY . .

# تشغيل البوت عند بدء تشغيل الحاوية
CMD ["python", "yot.py"]
