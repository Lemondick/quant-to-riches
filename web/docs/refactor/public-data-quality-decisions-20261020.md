# 阶段 C 实现取舍与审查记录

保持原 UI、独立工作区，不合并 main、不部署生产。以下按决定顺序保留理由与错误成本。

- Pre-flight: Task 3 weekly frequency lacks a verified weekday contract. Ruling: only apply weekly freshness when a concrete publication schedule is supplied; otherwise freshness unknown — cannot invent fund release weekdays — cost: more honest unknown labels until verified.

- Pre-flight: Existing Worker uses concurrent resident threads plus provider thread pools. Ruling: pass immutable calendars in provider instances cached by snapshot with bounded LRU; no mutable process-global calendar — prevents cross-task leakage — cost: small bounded duplication of provider caches per calendar revision.

- Task 1: Ruling: cross-year latest-session traversal is exercised in Task 2, where latest_session is consumed; Task 1 exposes unknown TradingDay on missing year — keeps interface boundaries coherent — cost: traversal bug gate lands in next commit.

- Task 2: Ruling: update old test literals trading/break to explicitly required continuous_trading/lunch_break, while frontend still accepts old payload names — fulfills six-state spec without changing layout — cost: external clients must consume documented new phase names.

- Task 2: Ruling: current SSE July 2026 effective rules section 2.4.2 and deferred-provisions attachment were checked, instead of applying outdated investor FAQ timing to current instruments — source https://www.sse.com.cn/lawandrules/sselawsrules2025/fund/trading/c/c_20260424_10817739.shtml — cost: future rule changes need new evidence/version.

- Task 3: Ruling: weekly schedule anchors explicit NAV valuation weekday and configured publication lag (default 1 trading session), not an invented publisher weekday — missing anchor remains unknown — cost: a verified non-default release contract must configure its lag.

- Task 3: Ruling: unknown publication policy keeps quality unknown and marks prior-date NAV as old evidence; retain actual value/date, never treat it as current estimate — satisfies fail-closed and old-evidence requirements together — cost: some genuine latest records remain unconfirmed until type/frequency is verified.

- Task 3: Ruling: preserve explicitly configured legacy natural-day cap only as stricter stale marking, remove default 14-day rejection — caller compatibility without losing observations — cost: legacy callers should migrate to shared session policies.

- Task 4: Ruling: the Eastmoney clist total is an authoritative quote-list count, not the registry/catalog total; sidemenu bklist and Sina do not publish an authoritative registry total. Keep catalog total/complete unknown for both, expose quote_total separately — no received count masquerades as supplier total — cost: global catalog completeness stays unknown even when every supplied page was fetched.

独立审查两项 Important：非法偏移归一化、目录缓存回退状态丢失，均先 RED 后 GREEN；无 Critical、无 Minor。刷新器调度按需求归阶段 D；缺少海外日历、竞价契约和供应商目录总数继续保持未知。
