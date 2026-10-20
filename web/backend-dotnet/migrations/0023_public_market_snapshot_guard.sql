-- Skip empty market publications so restarts can still recover the last observation.
-- This affects only the derived public cache, never account or financial history.
CREATE FUNCTION quant_public_market_nonempty() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kind = 'market.snapshot' THEN
        IF NOT COALESCE(CASE
            WHEN jsonb_typeof(NEW.result_json->'indices') = 'array'
                AND jsonb_typeof(NEW.result_json->'fund_rankings') = 'array'
                AND jsonb_typeof(NEW.result_json->'sector_rankings') = 'array'
            THEN jsonb_array_length(NEW.result_json->'indices')
                + jsonb_array_length(NEW.result_json->'fund_rankings')
                + jsonb_array_length(NEW.result_json->'sector_rankings') > 0
            ELSE false
        END, false) THEN
            RETURN NULL;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER public_market_nonempty
BEFORE INSERT OR UPDATE ON public_compute_snapshots
FOR EACH ROW EXECUTE FUNCTION quant_public_market_nonempty();
