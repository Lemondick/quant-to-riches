ALTER TABLE model_call_costs ADD COLUMN job_id TEXT;
ALTER TABLE model_call_costs ADD COLUMN job_attempt INTEGER;
ALTER TABLE model_call_costs ADD COLUMN owner_token TEXT;
ALTER TABLE compute_jobs ADD COLUMN worker_id TEXT;
CREATE INDEX model_cost_live_job ON model_call_costs(user_id,job_id) WHERE status='reserved';
CREATE INDEX compute_publication ON compute_jobs(kind,status,deadline);
