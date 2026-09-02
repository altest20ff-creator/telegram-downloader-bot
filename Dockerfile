# استخدام أحدث إصدار خفيف وثابت من Python
FROM python:3.10-slim

# منع بكتيريا تخزين التخزين المؤقت وتوجيه مخرجات الطباعة مباشرة للسجلات
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# تثبيت ffmpeg والأدوات الأساسية التي تحتاجها مكتبة yt-dlp لمعالجة مقاطع الفيديو
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# نسخ ملف المتطلبات وتثبيت المكتبات بأحدث الإصدارات
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools \
    && pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات المشروع (بما فيها yot.py و cookies.txt إن وجد)
COPY . .

# الأمر الرئيسي لتشغيل ملفك مباشرة
CMD ["python", "yot.py"]
