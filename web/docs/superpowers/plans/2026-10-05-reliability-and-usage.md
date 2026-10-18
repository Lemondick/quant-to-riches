# Reliability and usage improvements implementation plan

> **For agentic workers:** Use test-driven-development and dispatching-parallel-agents for independent domains; each task owns its listed files and is reviewed at integration.

**Goal:** Fix the five reproduced reliability defects and deliver the six user-approved enhancements in the existing multi-user web application.

**Architecture:** Keep authenticated account dispatch and isolated DuckDB files. Add focused services and routers, expose them through existing ledger/settings/admin pages, and retain explicit preview/confirmation for financial writes. Public data never becomes verified personal evidence automatically.

**Tech Stack:** Python 3.12, FastAPI, DuckDB, SQLite account registry, Vue/uni-app.

**Spec:** The accepted project review in this conversation (2026-10-05).

## Global constraints

- Registration remains limited to 30 total accounts, including the administrator.
- Never alter real account data during development or invoke paid models for tests.
- Never publish, push, enable automatic trading, or install external scheduled jobs.
- Historical replay must not earn forward-session credit. Unknown costs remain unknown.
- Every financial change uses a checked preview, explicit confirmation, atomic writes and an audit record.
- Preserve preexisting workspace changes and secret exclusions.

## Review focus

- Cross-account reads, alerts, previews and session revocations must be rejected or isolated.
- Stale previews, concurrent writes and duplicate confirms must not change balances twice.
- Missing baseline, historical company actions and incomplete prices must fail closed during replay.
- Active workers must finish before storage/site locks are released.
- Legacy single-user backups and retrospective sessions must not satisfy site/forward admission.

## Task 1: Evidence admission (independent)

Files: production_readiness.py, decision_repository.py, daily_decision.py, paper_readiness_report.py, record_forward_observation.py; focused forward-session helper and acceptance script.

- [x] Reproduce stale/backdated/weekend/unreconciled session credits and legacy-only backup admission in temporary stores.
- [x] Centralize valid forward-session eligibility including observation provenance, calendar, frozen strategy and reconciliation; all consumers use it.
- [x] Use full-site archive verification for operations admission; legacy archives remain readable but do not satisfy site readiness.
- [x] Prove valid sessions pass and historical replays, wrong versions and incomplete archives fail.

## Task 2: Runtime, costs and alerts (independent)

Files: advisor_scheduler.py, user_context.py, auth_app.py; focused runtime/cost/alert services, routers and Vue panels. Do not edit account registry/routes or production_readiness.py.

- [x] Reproduce still-running stop and healthy readyz with a broken business database.
- [x] Drain workers before closing storage; distinguish liveness and readiness with per-account degradation.
- [x] Show task activity, failures and verified site-backup metadata in administrator runtime view.
- [x] Capture available provider token usage, support explicit user-supplied prices and monetary limits, never invent missing costs.
- [x] Add per-user deduplicated alert center for data staleness, drawdown, fee evidence and task failure, with configurable thresholds/acknowledgment.
- [x] Verify account isolation, thresholds, missing usage/prices and shutdown ordering with temporary fixtures.

## Task 3: Account maintenance and administrator timeline (independent)

Files: services/auth.py, models/repositories/auth.py, controllers/auth_routes.py and admin_routes.py; focused timeline helper and Vue admin/account/login panels. Do not edit auth_app.py or user_context.py.

- [x] Add tests for detailed-view/export auditing before changing routes.
- [x] Record administrator access target/category/range without financial payload or secrets.
- [x] Provide paged administrator user-event timeline and 30-day daily trends from real business/account events.
- [x] Let users view/revoke sessions and revoke other devices; administrator issues expiring one-use password recovery token, resetting revokes all sessions.
- [x] Verify ordinary-user denial, token expiry/reuse, auditing, privacy and cross-account session denial.

## Task 4: Reconciliation and controlled historical replay (root)

Files: focused reconciliation/replay services and router, existing business app inclusion, ledger Vue panels and temporary acceptance scripts.

- [x] Test saved platform snapshot vs main-ledger differences, missing evidence and explicit resolution records.
- [x] Provide persistent reconciliation comparisons and explanations; no automatic modification of holdings.
- [x] Preview a missing historical transaction against a preserved baseline and event history in a temporary database, showing balance/holdings/NAV impact.
- [x] Require complete replay evidence, unchanged source fingerprint, explicit confirmation and atomic replay with original-record audit preservation.
- [x] Prove failed/duplicate/stale preview leaves balances unchanged and successful replay produces hand-checked balances.

## Task 5: Integration and delivery

- [x] Review domain changes and interfaces, run all new acceptance scripts plus existing backend/frontend acceptance.
- [x] Run static/format/type checks and frontend build; add meaningful checks to CI.
- [x] Update user/API/maintenance/status documentation and preserve realistic strategy evidence limitations.
- [x] Review the existing web migration and secret exclusions before a recoverable local Git checkpoint; do not push.

## Decisions

- User explicitly asked to implement the accepted review; no repeated design approval is needed.
- Work in the existing checkout because the active web migration is untracked and another worktree would omit the user's project.
- Separate agents own disjoint files; root performs shared integration and verification.

## Verification

- Final backend offline discovery: 63/63 passed (133.591 seconds).
- Four frontend Vue acceptance scripts, ESLint, Prettier and isolated H5 build passed.
- Backend Ruff, source-size gate, secret scan and 11 core typed source files passed.
- Finance cross-review reproduced snapshot contract and ordering defects; regression fixes verified.
- Integration reviewer could not complete because of its account usage limit; root reviewed integration and ran all acceptance checks.
- Money budgets fail closed for current providers without verified pre-call token ceilings.
- No actual account writes, paid calls, archive research rewriting, browser visual QA or Docker/public deployment.
- Git staged-content checks passed: no whitespace errors or private state/credentials added; secret scan clean. Local checkpoint includes the preexisting public web migration.
