FROM python:3.12-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 1. Системные библиотеки для Chromium Playwright.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgtk-3-0 \
    libnss3 \
    libnspr4 \
    libx11-xcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxi6 \
    libxtst6 \
    libxrandr2 \
    libasound2 \
    libgbm1 \
    chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 2. Устанавливаем Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Используем Chromium из Debian, без скачивания браузера и FFMPEG
# с нестабильного CDN Playwright.
ENV CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

# 4. Копируем код
COPY . .

# 5. Создаем директорию для маппинга
RUN mkdir -p /app/data/tenders/eis


CMD ["celery", "-A", "tasks", "worker", "--loglevel=info"]
