FROM python:3.12-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 1. Сначала обновляем индексы и ставим необходимые для Firefox системные библиотеки
# Это действие отделено от установки Playwright, чтобы повысить надежность
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
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 2. Устанавливаем Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Скачиваем сам браузер (без --with-deps, так как мы всё уже поставили выше)
RUN playwright install firefox

# 4. Копируем код
COPY . .

# 5. Создаем директорию для маппинга
RUN mkdir -p /app/data/tenders/eis

CMD ["celery", "-A", "tasks", "worker", "--loglevel=info"]