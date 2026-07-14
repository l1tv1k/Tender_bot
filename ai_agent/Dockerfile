FROM python:3.12-slim

# Отключаем кэш байт-кода и буферизацию вывода (важно для логов в Docker)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Создаем директорию для маппинга файлов тендеров от парсера
RUN mkdir -p /app/data

# Запускаем агента в режиме ожидания задач
CMD ["python", "main.py"]