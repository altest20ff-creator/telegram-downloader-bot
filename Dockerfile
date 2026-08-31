FROM python:3.10-slim

# تثبيت مكتبة ffmpeg والاعتمادات الأساسية
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# نسخ جميع الملفات داخل الحاوية
COPY . /app/

# تثبيت مكتبات بايثون من requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# أمر تشغيل البوت المباشر
CMD ["python3", "app.py"]
