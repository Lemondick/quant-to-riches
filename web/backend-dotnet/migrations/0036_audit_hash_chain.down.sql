-- 0036_audit_hash_chain.down.sql — 回滚 0036：移除哈希链列。
-- 警告：回滚将永久丢失已计算的链数据（hash/prev_hash）；如需保留证据，
-- 请先备份 admin_audit 表。幂等写法。

ALTER TABLE public.admin_audit DROP COLUMN IF EXISTS hash;
ALTER TABLE public.admin_audit DROP COLUMN IF EXISTS prev_hash;
