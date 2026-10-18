-- Do not silently reconcile existing duplicates: applying this index deliberately
-- fails if an earlier version already wrote conflicting external identifiers.
CREATE UNIQUE INDEX transactions_source_external_id
    ON transactions (user_id, source, external_id)
    WHERE source IS NOT NULL AND external_id IS NOT NULL;
