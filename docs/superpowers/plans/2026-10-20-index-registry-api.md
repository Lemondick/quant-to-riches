# Index registry and API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Complete Doc02 StageA: one16-item index identity registry and compatible real-source API, independent per-item failure, no fabricated values or source clocks.

**Architecture:** Keep existing market.snapshot, native.NET gateway, Python providers, shared embedded JSON-resource pattern and original Vue UI. Store definitions once in backend providers/index_registry.json, load frozen Python/C# definitions and expose definitions/status through existing snapshot for future Vue consumers. Registry includes stable ID,market,currency,IANA zone,calendar ID,source mappings,explicit quote/history verification statuses,display order,product provisional status and evidence. Quote adapters verify exact supplier identity/names/source namespace, numerical consistency, timestamp timezone, and currency metadata/basis; do not synthesize provider codes. Tencent known CN/HK/US mappings and Yahoo exactNikkei INDEX/JPY/AsiaTokyo identity have live2026-10-20 evidence. This phase does not implement new UI/fund classification. New foreign schedules must remain unknown rather than use CN until verified own-market session/calendar support is introduced in subsequent phases.

**Tech Stack:** ExistingPython/pandas/requests/PublicHttp, C#System.Text.Json embedded resource, existingDTOs/API/tests and GitHubLinuxCI. No new dependency or DB migration expected.

**Spec:** C:/Users/admin/Desktop/02-%E5%8A%9F%E8%83%BD%E4%BC%98%E5%8C%96%E4%B8%8E%E5%8A%9F%E8%83%BD%E8%A1%A5%E5%85%85%E5%AE%9E%E6%96%BD%E9%9C%80%E6%B1%82.md sections2-6,10-13 StageA, plus history API compatibility groundwork from7/11. User supplied written design and authorized document-order execution in isolated workspace; preserve original UI and no mainmerge/proddeploy.

## Global Constraints

- Exactly16 unique stable IDs; first15 product-confirmed from user's spec; cn.csi.1000 product provisional, not screenshot-confirmed. Do not block15 on unresolved original16th.
- Unknown/unverified source mappings cannot be requested in production. Identity/name/time/currency checks and numerical checks; never substitute Nikkei futures or NASDAQ100 for requested cash indices.
- Source time, trade date, fetched_at, cache_read_at distinct. No fake zero/unavailable values in successful quote arrays, no fake candles/sparklines.
- Legacy4 history symbols and existing fund/sector/account routes remain compatible; funds and vendor-qualified sector taxonomies stay independent.
- Per-index cache isolated by stable ID, source clocks retained on fallback; a failure cannot remove or relabel other indices. Public definitions/status contain no account data.
- No main merge,production deploy,real ledger mutation/trade,credential use or broad Vue redesign. Keep current bounds/deadlines/nonoverlap and recovery tooling.

## Review Focus

- Same six-digit code could identify a stock under another namespace; explicit registered provider identity/type/name must distinguish indices, and unknown index codes must not default to Shanghai.
- A failed overseas item must preserve other15 and its own last-valid clock; provider-return order and duplicate/extra rows cannot change ID associations.
- Nikkei provider instrumentType,currency,zone must match INDEX/JPY/AsiaTokyo, never cash-like futures; foreign naive source clocks assigned only verified provider timezone, no CN fallback.
- Legacy four-history cache keys remain stable; unsupported registered index returns explicit history-unavailable capability without queueing or serving another index's data; conflicting id/symbol rejected.
- Shared JSON actually packaged into Python and.NET; a new definition cannot require hand-editing C#/Vue identity lists. Provisional product evidence remains distinct from verified provider mapping.

## Task 1: Unique registry and verified identity evidence

**Files:** new providers/index_registry.json,index_registry.py; new Core/Market/IndexRegistry.cs and Core.csproj resource; tests scripts/verify_index_registry.py and Core.Tests/IndexRegistryTests.cs; docs/refactor/index-registry-evidence-20261020.md.

**Interfaces:** Frozen definitions get_index(id), resolve_legacy_index(symbol), definitions(); C#IndexRegistry.All/Get/ResolveLegacy. JSON snake_case,format_version1,registry_version,definitions[], provider mapping includes explicit symbol,response_symbol,name aliases,namespace/type,currency/timezone basis and evidence. History4 verified only until separate history probes; others pending. Product status confirmed/provisional separate from quote verification.

- [x] Inspect existing design assets/tests for original16th; if no evidence retain configurable provisionalCsi1000, record exact result. Verify live quote identities; official000888 is SSE Composite Total Return, Nikkei cash identity confirmed. Store small sanitized observed metadata and source links, no permanent live-price fixtures masquerading as current data.
- [x] RED registry tests16unique/order/market/currency/zone/calendar completeness, every enabled mapping verified with evidence, provisional16th, legacy4 aliases, unknown/stock code refusal, no duplicateNikkei, shared resource packaging.
- [x] Implement only authority JSON and strict immutable Python/C#readers, remove default inferred index mapping in market_feed_parsing and derive existing mappings from registry. Run target/source/build checks; commit.

## Task 2: Per-index real quote adapters and snapshot contract

**Files:** new providers/index_quotes.py; market_feed.py,controllers/schemas.py,data_quality.py onlyneeded integration; new scripts/verify_index_quotes.py and Worker tests/test_index_quotes.py.

**Interfaces:** IndexQuoteProvider takes existingPublicHttp/clock/calendar, fetches registered providers with bounded concurrency; returns actualMarketQuote rows with id,market,currency,timezone/calendar,source metadata plus index_statuses[] for alldefinitions. Existing price/change/change_rate fields retained; add value/change_percent compatibility if useful without replacing originals. Snapshot adds index_registry version/definitions and per-index statuses, valid quote arrays only.

- [x] RED exactname/symbol/namespace mismatches, shuffled/duplicate/extra rows, one source/item failure, nonfinite/price-change mismatch, JPfutures/currency/zone mismatch, foreign clockzones/unknown calendars, per-ID stale fallback original timestamps, no fabricated0, unchanged stock quote behavior.
- [x] Implement Tencent registered batchCN/HK/US and exactYahooNikkei adapter with strictidentity checks; source currency absent is explicitly reported as registry/official basis, never pretend provider supplied it; unsupported/unverified mapping notrequested. Bounded no-redirectPublicHttp and per-item cache, no cross-source tick stitching.
- [x] Extend MarketQuote/MarketSnapshot DTOs and existing snapshot loader, preservefund/sector sections and previous error semantics. Run contract/worker/backward compatibility/source tests; commit.

## Task 3: Native registry projection, API and history compatibility

**Files:** MarketController.cs,PublicMarketSnapshot.cs,PublicSnapshotRefresher.cs,PublicDataQuality.cs as needed; worker/public_charts.py, public_operations.py; existing/frontend services/dataApi.ts types only; new API integration/unit tests.

**Interfaces:** existing market.snapshot exposes registry and item statuses; existing market.history accepts preferredindex_id and old4symbol aliases. Registered unconnected history returns items[]/quality unavailable/reasonhistory_not_connected with exactid, nojob/no500. Old4 requests canonicalize to oldsymbol worker/cache inputs, append registryidentity to response. Refresher lists derive from registry capabilities; no parallel names/ticker lists.

- [x] RED native snapshot projection legacy rows and per-item status, wrongID/foreignmarket neverrestampedCN, old4symbol/cache compatibility, index_id+symbol conflicts, unsupportedhistory capability and noqueuedjob, publicregistry no accountfields.
- [x] Implement registry-driven projection and validated history routing; derive history dictionary/refresher legacy quotes from declared capabilities; preserve currentfour-source history implementation until StageC. Add officialDTOindexdefinition/status/quote fields, no component/layout edits.
- [x] Run source/unit/realPGCI and existing history tests; verify no DB migration or added parallel API needed. Commit.

## Task 4: StageA acceptance

- [ ] Full exact-source Linux CI with PG/Worker0skip, frontend/browser/build. Fresh whole-branch reviewer per executing-plans; Important/Critical RED->GREEN once and fullgate; archive all rulings/minors.
- [ ] Update docs/API/evidence with actual source/counts, source capabilities and limitations, registry ownership and provisional16th. Report stage summary then continue Doc02 StageB original UI indexstrip; no repeated permission or merge/deploy.
