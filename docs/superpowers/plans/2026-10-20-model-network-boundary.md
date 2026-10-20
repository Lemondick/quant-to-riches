# Model network boundary and deployment contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete Doc01 Stage F / BUG014 and current deployment-contract documentation without deploying, merging main, invoking paid models, or modifying production ledger data.

**Spec:** C:/Users/admin/Desktop/01-%E5%BD%93%E5%89%8D%E9%97%AE%E9%A2%98%E4%BF%AE%E5%A4%8D%E4%B8%8E%E7%A8%B3%E5%AE%9A%E6%80%A7%E6%95%B4%E6%94%B9%E9%9C%80%E6%B1%82.md sections7-10.

**Architecture:** Preserve support for public custom compatible HTTPS API hosts on443; do not invent an existing vendor allowlist. Current .NET catalog validates all addresses and pins sockets; native web/worker/public_model_http.py pins public targets and preserves hostname TLS. Legacy isolated research main.py needs the same runtime guarantee using only standard-library modules (it is launched with -I). Resolve all targets at connect time, reject any nonpublic result before creating a socket, connect directly to numeric sockaddr, default verified TLS using original hostname. One total TCP/TLS setup budget; no proxy, redirect, or new HTTP retry. Retain existing one definite429 retry budget and sanitized error codes. Permit documented simple compatible API base prefixes and full chat/completions path; reject credentials, query/fragment, non443, controls, escaped/dot/double-slash path ambiguity. A strict vendor allowlist would break current custom-domain product scope; deployment egress controls remain additional defense.

**Global Constraints:** Independent codex/ branch, preserve original dirty workspace and all11 Vue pages/theme. No main merge/production deploy/real financial mutations/actual model API use. Keep existing backup/restore/rollback/fingerprint tooling. All test network and key fixtures synthetic/mocked. No secrets or full credentialed URL in errors/logs.

## Review Focus

- DNS rebinding validation must happen inside actual connect, with every result validated before any dial; no second hostname lookup.
- IPv4-mapped IPv6, unspecified/multicast/reserved/linklocal/private and mixed public-private DNS answers must fail closed.
- Original host in SNI, certificate hostname verification and HTTP Host; default verification never disabled.
- Socket/TLS failure may try another already-validated public IP only before POST, within one total setup deadline; no duplicate charged POSTs after read failure.
- Missing key causes no DNS/socket; failures do not echo secrets or credentials in URLs; URL parsing errors are sanitized.
- Docs distinguish current native production entry from legacy compatibility code, dynamic complete migrations through0023, refresh default30s and bounds15..300 plus task55/overall75 semantics; historical CI/deploy claims are labeled and current verification points to exact commit.

## Task 1: Runtime pinned HTTPS boundary

**Files:** legacy integrations/tradingagents_worker/main.py, new standard-library public_https.py; new scripts/verify_model_network_boundary.py. Touch native transport only for reproduced failures within same boundary requirement.

- [x] Inventory actual endpoint/transport/config boundary, record chosen supported path/custom-host policy.
- [x] RED mocked regressions for private/mixed/rebound DNS, URL credentials/query/fragment/ports/path controls, mappedIPv6; verify missing key no network and original TLS/SNI/Host with public pinned socket.
- [x] Implement runtime resolution and validated numeric direct connect, verified original-host TLS, bounded setup and sanitized error mapping. Keep adapters standard-library-only and existing parent-PID/process restrictions.
- [x] Exercise IPv4/IPv6 fallback and setup/read deadlines, socket close and no POST replay on failure; existing research/native Worker suites and source checks pass; commit.

## Task 2: Current deployment and documentation contracts

**Files:** README.md, web/README.md, web/backend-dotnet/ARCHITECTURE.md, web/frontend/README.md, web/docs/refactor/p7-runbook.md, web/backend-dotnet/deploy/ubuntu-runbook.md, current StageF evidence/decision notes. CI/migration source changes only if actual mismatch requires them.

- [ ] Compare actual migrations through0023, CI job commands/results, native startup/publish config, refresh defaults and model boundary to docs.
- [ ] Correct current instructions, mark legacy FastAPI/container/exchange entry as compatibility only and distinguish historical release evidence from undeployed branch. Preserve recovery/fingerprints and explain migration compatibility, no blanket claim no new migrations.
- [ ] State exact source-bound tests and any skipped/unknown operational evidence; source/build/doc-contract checks pass. No redundant behavioral tests for prose-only edits; commit.

## Task 3: Final Doc01 acceptance

- [ ] Exact-source Linux CI green: realPG/Worker no skips; frontend/browser/build pass. One required fresh whole-branch read-only review, fix Important/Critical RED->GREEN once, then full gate.
- [ ] Archive exhaustive ledger rulings/minors and Doc01 PASS/FAIL/SKIPPED checklist with evidence and real limits, no deployment claims. Continue Doc02 in order under same original authorization, no repeated approval.
