-- User-confirmed bill evidence is distinct from immutable call-time token prices.
ALTER TABLE model_call_costs ADD COLUMN bill_evidence_json JSONB;
