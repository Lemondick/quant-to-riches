# Public Market Data Quality Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline. No subagent delegation is required. Steps use checkbox (`- [x]`) syntax.

**Goal:** Verify and repair public quote, ETF, sector, profile, NAV and estimate semantics; display only trustworthy per-module update times without redesigning the UI.
**Architecture:** Extend the existing Python normalization/DTO and in-process caches with shared bounded HTTP and quality helpers. Keep existing .NET gateway, routes and persistent public snapshot mechanism; add no database schema or service. Add only timestamp text to current Vue headings, with internal adapters for separated official NAV fields.
**Tech Stack:** Python 3.12, requests, dataclasses/Pydantic, ASP.NET Core 10, Vue 3/TypeScript, existing unit and browser tests.
**Spec:** C:/Users/admin/.codex/attachments/0ec0daed-5078-410a-89d8-aff36beb3551/已粘贴的文本.txt

## Global Constraints
- Frontend visible changes limited to module update-time text; preserve layout hierarchy, styles, routes and operations.
- No Redis/MongoDB/new PostgreSQL service/schema; bounded process-memory caching and existing runtime only.
- Never substitute fetch/cache-read time for a source timestamp or invent time precision.
- Official NAV and intraday estimates are separate fields; unavailable estimates remain null.
- Preserve source taxonomies and transport provenance; AKShare is a library, not an independent supplier.
- Do not bypass upstream authentication/challenges/rate limits or enable unverified candidates.

## Review Focus
- A formally dated NAV must not become an estimate, including old persisted snapshot reads.
- Weekend/holiday closing observations must not be invalidated just because their time is unchanged.
- Legacy clients must not convert nullable new estimate/quote fields into false zeros.
- Partial/duplicated/revised NAV history must not silently produce total returns across corporate actions.
- Upstream errors and concurrent refreshes must stay bounded and must never log credentials.

### Task 1: Audit and evidence
- [x] Save baseline sources and frontend build; inspect all DTO/callers.
- [x] Probe real endpoints once in bounded batches; record status, redirect, format, identifiers, units and clocks.
- [x] Write module findings and source dependency registry before enabling fallback candidates.

### Task 2: Shared transport, clock semantics and memory cache
- [x] Failing tests for redirects/statuses/HTML/empty/parse errors, retry/circuit limits, bounded cache and same-key merging.
- [x] Implement small shared helpers and trading-calendar-aware quality classification; keep fetched/read/source times distinct.
- [x] Verify no stale cache result receives a new updated_at and no source appears independently redundant through an alias.

### Task 3: Fund valuation, then index/ETF normalization
- [x] Reproduce official-NAV-as-estimate, stale/future estimate acceptance and wrong Shanghai ETF prefix.
- [x] Separate official/estimate fields, validate identity/finite values/units/timestamps, and normalize current consumers safely.
- [x] Validate equivalent fallback quotes against real samples and test stale-primary/fresh-backup and both-stale cases.

### Task 4: Sector and fund-history/profile semantics
- [x] Cache taxonomy separately, preserve vendor-qualified IDs, support observed diff variants and isolate unavailable prices.
- [x] Validate fund-code identity, source per field, conflicts, actual report dates and low-frequency TTLs.
- [x] Validate NAV uniqueness/dates/actions/coverage; calculate only supported returns with explicit start/end/methodology.

### Task 5: Compatible API and timestamp-only UI
- [x] Adapt old cached results safely without introducing false zero values.
- [x] Add metadata to existing contracts; cache read time stays separate.
- [x] Add per-module title text only; dates retain date precision and unknown time renders 更新时间：未知.
- [x] Compare before/after screenshots with timestamp text masked; preserve component/route/style structure.

### Task 6: Verification and delivery
- [x] Run all relevant worker/frontend/.NET checks and simulated failure matrix.
- [x] Compare bounded actual 5-index, 5-board, 5-ETF and 5-fund samples with aligned type/date/time; report unverifiable comparisons honestly.
- [x] Document changed files, before/after samples, mapping, health, tests, unavailable endpoints and resource impact.
- [x] Stage/deploy under existing authorization only after checks; verify authenticated production reads and preserve financial fingerprints, then close SSH.
