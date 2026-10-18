ALTER TABLE attribution_reports
    DROP CONSTRAINT attribution_reports_data_source_check,
    ADD CONSTRAINT attribution_reports_data_source_check
        CHECK (data_source IN ('manual_sector_input', 'native_static_price', 'native_linked_brinson'));
