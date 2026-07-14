"""
Внутренний API — заглушка (раздел 7.2 ТЗ).
Следующий шаг: эндпоинты для получения списка тендеров, карточки,
простановки статуса — которые дальше будет использовать бот.
"""
from fastapi import FastAPI

app = FastAPI(title="Tender Service API")


@app.get("/health")
def health():
    return {"status": "ok"}
