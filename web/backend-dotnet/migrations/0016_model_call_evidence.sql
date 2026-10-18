ALTER TABLE model_call_costs ADD COLUMN role TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE model_call_costs ADD COLUMN provider TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE model_call_costs ADD COLUMN stage TEXT NOT NULL DEFAULT 'legacy_unknown';
ALTER TABLE model_call_costs ADD COLUMN unknown_reason TEXT;
UPDATE model_call_costs SET unknown_reason='legacy_unknown' WHERE cost IS NULL AND status<>'reserved';
