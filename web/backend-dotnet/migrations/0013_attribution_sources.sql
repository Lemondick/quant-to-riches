ALTER TABLE attribution_reports
    ADD COLUMN data_source TEXT NOT NULL DEFAULT 'manual_sector_input'
        CHECK (data_source IN ('manual_sector_input', 'native_static_price')),
    ADD COLUMN source_json JSONB NOT NULL DEFAULT '{}'::jsonb;
