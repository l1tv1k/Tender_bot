"""
Parser Service — заглушка для проверки инфраструктуры.

Следующий шаг (шаг 2 из плана): заменить эту заглушку на реальный
прототип на camoufox + Playwright, который ищет закупки по охране
на zakupki.gov.ru (44-ФЗ) и печатает найденные карточки в консоль,
без сохранения в БД. Только когда прототип подтвердит, что скрапинг
устойчиво работает — переносим логику сюда для сохранения в БД.
"""

import os
import time

import redis
import psycopg2


def wait_and_check_infra():
    r = redis.Redis(host="redis", port=6379)
    conn = psycopg2.connect(
        host="postgres",
        dbname="tenders",
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM tenders;")
    tenders_count = cur.fetchone()[0]
    r.ping()
    print(f"[parser] Инфраструктура ОК. Redis доступен, тендеров в БД: {tenders_count}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    while True:
        try:
            wait_and_check_infra()
        except Exception as e:
            print(f"[parser] Ошибка подключения к инфраструктуре: {e}")
        time.sleep(30)
