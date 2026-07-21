-- Migrates the legacy analysis table (reestr_number, analysis_data) to the
-- schema used by the parser, AI worker, and Telegram bot.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'tender_analysis'
          AND column_name = 'reestr_number'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'tender_analysis'
          AND column_name = 'tender_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'tender_analysis_legacy'
    ) THEN
        ALTER TABLE tender_analysis RENAME TO tender_analysis_legacy;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS tender_analysis (
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

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'tender_analysis_legacy'
    ) THEN
        INSERT INTO tender_analysis (
            tender_id, labor, pricing, object_info, requirements,
            financial_terms, risks, summary, analysis_status, analyzed_at
        )
        SELECT
            t.id,
            legacy.analysis_data -> 'labor_costs',
            legacy.analysis_data -> 'costs',
            legacy.analysis_data -> 'protected_object',
            legacy.analysis_data -> 'requirements',
            legacy.analysis_data -> 'financials',
            legacy.analysis_data -> 'risks',
            legacy.analysis_data ->> 'summary',
            'completed',
            legacy.created_at AT TIME ZONE 'UTC'
        FROM tender_analysis_legacy AS legacy
        JOIN tenders AS t ON t.reestr_number = legacy.reestr_number
        ON CONFLICT (tender_id) DO NOTHING;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_analysis_risks ON tender_analysis USING GIN (risks);
CREATE INDEX IF NOT EXISTS idx_analysis_requirements ON tender_analysis USING GIN (requirements);
