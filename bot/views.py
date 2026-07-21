from datetime import datetime
from html import escape

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

PAGE_SIZE = 5

STATUS_MAP = {
    "lost": "🔴 Проиграли",
    "won": "🟢 Выиграли",
    "skip": "🟣 Не идём на тендер",
    "doubt": "🟡 Под сомнением",
    "submitted": "🔵 Подали заявку",
    "unset": "⚪ Не указан",
    "target": "🔷 Целевой тендер",
}

STATUS_CODES = {
    "lost": 1,
    "won": 2,
    "skip": 3,
    "doubt": 4,
    "submitted": 5,
    "unset": 6,
    "target": 7,
}


def format_money(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.2f}".replace(",", " ")


def format_dt(value) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    return str(value)


def short(text: str | None, limit: int = 180) -> str:
    value = " ".join((text or "—").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def get_status_keyboard(reestr_number: str) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for callback_data, label in STATUS_MAP.items():
        builder.button(text=label, callback_data=f"status:{callback_data}:{reestr_number}")

    builder.button(text="↻ Анализ", callback_data=f"reanalyze:{reestr_number}")
    builder.button(text="📁 Документы", callback_data=f"docs:{reestr_number}")
    builder.button(text="↩ К списку", callback_data="list:0")
    builder.adjust(2, 2, 2, 1, 2)
    return builder.as_markup()


def get_list_keyboard(rows, page: int, has_next: bool) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        builder.button(text=f"Открыть {row['reestr_number']}", callback_data=f"card:{row['reestr_number']}")

    if page > 0:
        builder.button(text="← Назад", callback_data=f"list:{page - 1}")
    if has_next:
        builder.button(text="Вперёд →", callback_data=f"list:{page + 1}")

    builder.button(text="📤 CSV", callback_data="export:csv")
    builder.adjust(1, 2, 1)
    return builder.as_markup()


def get_docs_keyboard(rows, reestr_number: str) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows[:10]:
        builder.button(text=short(row["file_name"], 45), callback_data=f"senddoc:{row['id']}")
    builder.button(text="↩ К карточке", callback_data=f"card:{reestr_number}")
    builder.adjust(1)
    return builder.as_markup()


def get_parser_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔎 Запустить поиск", callback_data="parser:run")
    builder.button(text="🧪 Тест поиска", callback_data="parser:debug")
    builder.adjust(1, 1)
    return builder.as_markup()


def format_primary_card(reestr_number: str, basic_data: dict) -> str:
    nmck = basic_data.get("nmck", "—")
    region = basic_data.get("region", "—")
    title = escape(str(basic_data.get("title", "—")))
    deadline = format_dt(basic_data.get("submission_deadline"))
    source_url = basic_data.get("source_url", "")

    link = f'<a href="{escape(source_url)}">Открыть в ЕИС</a>' if source_url else "Открыть в ЕИС"
    return (
        f"⚡️ <b>Новый тендер</b>: <code>{escape(reestr_number)}</code>\n\n"
        f"📌 {title}\n"
        f"📍 Регион: {escape(str(region or '—'))}\n"
        f"💰 НМЦК: {nmck} руб.\n"
        f"⏰ Приём заявок до: {escape(deadline)}\n\n"
        f"⏳ ИИ-анализ запущен, карточка обновится автоматически.\n"
        f"🔗 {link}"
    )


def format_tender_list(rows, page: int, total: int) -> str:
    if not rows:
        return "Пока нет тендеров в базе."

    start = page * PAGE_SIZE + 1
    end = start + len(rows) - 1
    lines = [f"📋 <b>Тендеры {start}–{end} из {total}</b>"]
    for row in rows:
        lines.append(
            "\n"
            f"<b>{escape(row['reestr_number'])}</b> · {escape(row['status_name'])}\n"
            f"{escape(short(row['title']))}\n"
            f"📍 {escape(str(row['region'] or '—'))} · 💰 {format_money(row['nmck'])} руб.\n"
            f"⏰ {escape(format_dt(row['submission_deadline']))}"
        )
    return "\n".join(lines)


def format_full_card(row, docs_count: int = 0) -> str:
    source_url = row["source_url"] or ""
    link = f'<a href="{escape(source_url)}">Открыть в ЕИС</a>' if source_url else "Открыть в ЕИС"
    summary = row["summary"] or "ИИ-анализ ещё не готов."

    return (
        f"🚨 <b>Тендер {escape(row['reestr_number'])}</b>\n"
        f"📊 Статус: {escape(row['status_name'])}\n"
        f"🏛 Закон: {escape(str(row['law_type'] or '—'))}\n"
        f"📍 Регион: {escape(str(row['region'] or '—'))}\n\n"
        f"📌 {escape(row['title'])}\n"
        f"👤 Заказчик: {escape(row['customer_name'] or '—')}\n"
        f"💰 НМЦК: {format_money(row['nmck'])} руб.\n"
        f"⏰ Приём заявок до: {escape(format_dt(row['submission_deadline']))}\n"
        f"📦 Документов: {docs_count}\n"
        f"🌐 Статус площадки: {escape(str(row['status_on_platform'] or '—'))}\n\n"
        f"📝 <b>ИИ-анализ</b>\n{escape(summary)}\n\n"
        f"🔗 {link}"
    )


def format_full_ai_card(reestr_number: str, ai_data: dict, current_status: str = "Не обработан") -> str:
    nmck = ai_data.get("costs", {}).get("total_nmck", "—")
    posts = ai_data.get("labor_costs", {}).get("guard_posts", "—")
    source_url = ai_data.get("source_url", "")
    link = f'<a href="{escape(source_url)}">Открыть в ЕИС</a>' if source_url else "Открыть в ЕИС"

    return (
        f"🚨 Тендер: <code>{escape(reestr_number)}</code>\n"
        f"📊 Статус: {escape(current_status)}\n\n"
        f"💰 НМЦК: {escape(str(nmck))} руб.\n"
        f"👮 Посты: {escape(str(posts))}\n\n"
        f"📝 Сводка от ИИ:\n{escape(ai_data.get('summary', 'Нет данных'))}\n\n"
        f"🔗 {link}"
    )
