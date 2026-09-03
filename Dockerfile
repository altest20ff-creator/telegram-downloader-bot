FROM python:3.11-slim

# =========================================================
# إعدادات Python
# =========================================================

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# إضافة Deno إلى PATH
ENV PATH="/root/.deno/bin:${PATH}"

# =========================================================
# تثبيت FFmpeg + Deno
# =========================================================

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        unzip && \
    rm -rf /var/lib/apt/lists/*

# تثبيت Deno
RUN curl -fsSL https://deno.land/install.sh | sh

# =========================================================
# مجلد المشروع
# =========================================================

WORKDIR /app

# =========================================================
# تثبيت Python packages
# =========================================================

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# =========================================================
# نسخ المشروع
# =========================================================

COPY . .

# =========================================================
# تشغيل البوت
# =========================================================

CMD ["python", "yot.py"]
