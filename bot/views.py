from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

STATUS_MAP = {
    "target": "🔷 Целевой",
    "doubt": "🟡 Под сомнением",
    "work": "✅ В работе",
    "reject": "❌ Отказ"
}


def get_status_keyboard(reestr_number: str) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for callback_data, label in STATUS_MAP.items():
        builder.button(text=label, callback_data=f"status:{callback_data}:{reestr_number}")

    builder.button(text="📁 Скачать ТЗ", callback_data=f"doc:{reestr_number}")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def format_primary_card(reestr_number: str, basic_data: dict) -> str:
    """Карточка Молния (до работы ИИ)"""
    # basic_data - это данные, которые парсер берет прямо с сайта ЕИС
    nmck = basic_data.get('nmck', '—')
    region = basic_data.get('region', '—')

    msg = (
        f"⚡️ НОВЫЙ ТЕНДЕР (ПАРСЕР): {reestr_number}\n\n"
        f"📍 Регион: {region}\n"
        f"💰 НМЦК: {nmck} руб.\n\n"
        f"⏳ Ожидаю результаты ИИ-анализа (документы скачиваются)...\n\n"
        f"🔗 Открыть в ЕИС"
    )
    return msg


def format_full_ai_card(reestr_number: str, ai_data: dict, current_status: str = "Не обработан") -> str:
    """Полная карточка (ИИ отработал)"""
    nmck = ai_data.get('costs', {}).get('total_nmck', '—')
    posts = ai_data.get('labor_costs', {}).get('guard_posts', '—')

    msg = (
        f"🚨 Тендер: {reestr_number}\n"
        f"📊 Статус: {current_status}\n\n"
        f"💰 НМЦК: {nmck} руб.\n"
        f"👮 Посты: {posts}\n\n"
        f"📝 Сводка от ИИ:\n{ai_data.get('summary', 'Нет данных')}\n\n"
        f"🔗 Открыть в ЕИС"
    )
    return msg