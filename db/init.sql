-- Включаем расширение для генерации UUID
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Справочник статусов (Пункт 5.3)
CREATE TABLE tender_statuses (
    code INT PRIMARY KEY,
    name VARCHAR(50) NOT NULL
);

INSERT INTO tender_statuses (code, name) VALUES
    (1, '🔴 Проиграли'),
    (2, '🟢 Выиграли'),
    (3, '🟣 Не идём на тендер'),
    (4, '🟡 Под сомнением'),
    (5, '🔵 Подали заявку'),
    (6, '⚪ Не указан'),
    (7, '🔷 Целевой тендер');

-- 2. Пользователи
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    role VARCHAR(50) DEFAULT 'user',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Тендеры
CREATE TABLE tenders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    reestr_number VARCHAR(100) UNIQUE NOT NULL,
    platform VARCHAR(100) NOT NULL,
    law_type VARCHAR(50) NOT NULL,
    title TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    customer_inn VARCHAR(20),
    nmck NUMERIC(15, 2),
    submission_deadline TIMESTAMP WITH TIME ZONE,
    execution_start DATE,
    execution_end DATE,
    region VARCHAR(255),
    status_on_platform VARCHAR(100),
    source_url TEXT,
    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. Документы тендера
CREATE TABLE tender_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tender_id UUID REFERENCES tenders(id) ON DELETE CASCADE,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_hash VARCHAR(64) NOT NULL,
    version INT DEFAULT 1,
    downloaded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. ИИ-Анализ тендера
CREATE TABLE tender_analysis (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tender_id UUID UNIQUE REFERENCES tenders(id) ON DELETE CASCADE,
    labor JSONB,
    pricing JSONB,
    object_info JSONB,
    requirements JSONB,
    financial_terms JSONB,
    risks JSONB,
    summary TEXT,
    analysis_status VARCHAR(50) DEFAULT 'pending',
    analyzed_at TIMESTAMP WITH TIME ZONE
);

-- 6. Статусы тендеров в разрезе пользователей
CREATE TABLE user_tender_status (
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    tender_id UUID REFERENCES tenders(id) ON DELETE CASCADE,
    status_code INT REFERENCES tender_statuses(code) DEFAULT 6,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, tender_id)
);

-- 7. Лог уведомлений
CREATE TABLE notification_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tender_id UUID REFERENCES tenders(id) ON DELETE CASCADE,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,
    sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- === ИНДЕКСАЦИЯ (Пункт 5.4) ===

-- Полнотекстовый поиск (создаем tsvector "на лету" из названия и заказчика)
CREATE INDEX idx_tenders_fulltext ON tenders USING GIN (to_tsvector('russian', title || ' ' || customer_name));

-- Обычные индексы для быстрых фильтров
CREATE INDEX idx_tenders_reestr_number ON tenders(reestr_number);
CREATE INDEX idx_tenders_submission_deadline ON tenders(submission_deadline);
CREATE INDEX idx_tenders_region ON tenders(region);
CREATE INDEX idx_tenders_law_type ON tenders(law_type);

-- GIN индексы для самых востребованных JSONB-полей
CREATE INDEX idx_analysis_risks ON tender_analysis USING GIN (risks);
CREATE INDEX idx_analysis_requirements ON tender_analysis USING GIN (requirements);