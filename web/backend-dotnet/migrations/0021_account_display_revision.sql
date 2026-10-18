ALTER TABLE users ADD COLUMN data_revision bigint NOT NULL DEFAULT 0;
CREATE TABLE public_display_revision (id boolean PRIMARY KEY DEFAULT true CHECK(id), revision bigint NOT NULL DEFAULT 0);
INSERT INTO public_display_revision(id) VALUES(true);

CREATE FUNCTION quant_account_display_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        UPDATE users SET data_revision=data_revision+1 WHERE id=OLD.user_id;
    END IF;
    IF TG_OP <> 'DELETE' AND (TG_OP='INSERT' OR NEW.user_id IS DISTINCT FROM OLD.user_id) THEN
        UPDATE users SET data_revision=data_revision+1 WHERE id=NEW.user_id;
    END IF;
    RETURN NULL;
END;
$$;
CREATE TRIGGER position_display_revision AFTER INSERT OR UPDATE OR DELETE ON positions FOR EACH ROW EXECUTE FUNCTION quant_account_display_changed();
CREATE TRIGGER instrument_display_revision AFTER INSERT OR UPDATE OR DELETE ON instruments FOR EACH ROW EXECUTE FUNCTION quant_account_display_changed();
CREATE TRIGGER document_display_revision AFTER INSERT OR UPDATE OR DELETE ON user_documents FOR EACH ROW EXECUTE FUNCTION quant_account_display_changed();

CREATE FUNCTION quant_public_display_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE public_display_revision SET revision=revision+1 WHERE id=true;
    RETURN NULL;
END;
$$;
CREATE TRIGGER public_display_revision AFTER INSERT OR UPDATE OR DELETE ON public_compute_snapshots FOR EACH STATEMENT EXECUTE FUNCTION quant_public_display_changed();
