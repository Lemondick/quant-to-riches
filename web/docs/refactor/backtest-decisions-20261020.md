# Backtest correctness decisions — 2026-10-20

Plan: `docs/superpowers/plans/2026-10-20-backtest-correctness.md`; source verified at `e07aa833012fcb508e81e47de6c14eb2e73b3f63`.

- Task1: Ruling: use real close-to-close NAV observations; first recorded close is baseline, no invented opening/previous-market price — keeps auditable evidence consistent with existing calculate_performance — cost: opening-to-first-close return is not claimed and fold-boundary intervals must be disclosed.

- Task1: Ruling: fees are sum_fold_actual_fees and turnover is sum_fold_two_sided_turnover; relabel report explicitly — nominal fold amounts are not a simulated continuous-capital execution account — cost: downstream consumers must use aggregation metadata when comparing turnover.

- Task1: Ruling: require explicit trading_dates for OOS test outputs rather than infer session completeness from two matching possibly incomplete price tables — actual BacktestResult already supplies this; unknown expected sessions fail closed — cost: custom test runners must provide expected dates along with NAV/benchmark. Additional RED for missing date declaration; historical manual report RED4 corrected to unknown aggregation and research_only, no verified cost claims.

- Task3: Ruling: prefix recomputation measured 16.5474s vs original 0.5399s for 100 symbols x 500 synthetic sessions and 60 decisions (6000 feature rows); retain causal full-history preparation only inside engine-owned store and expose detached current/prior-week snapshots plus past dates and an opaque revision token to strategies — spec explicitly allows non-signal preparation isolation — cost: extra small feature-view interface and preparation memory; no future raw or prepared full frame may be attached to strategy/context. Raw price/instrument frames detach once per context to prevent observed pandas2 source mutation. RED first5 tests3 failures; prepared-view3 additional RED tests recorded.

- Task3: Ruling: actual builtin prepared-vs-prefix regression exposed preexisting buy-loop dict passed into Series-only _display_rank/_safe_score helpers (AttributeError dict.index), suppressing real candidate signals in both paths — use iterrows to honor existing helper contract, one-line surgical fix required for meaningful builtin regression — cost: minor row iteration overhead over at most configured candidate slots. RED test_builtin_prepared_and_prefix_paths_generate_the_same_signals captured actual callback errors before fix. Prepared missing amount input preserves original explicit strategy_error:ValueError via prefix fallback instead of raising whole replay.

Final fresh review: no Critical, Important, Minor or declined-to-judge items. No deferred minors. Independently reran31/31 focused tests. [Linux run39395377198](https://github.com/Lemondick/quant-to-riches/actions/runs/39395377198): Python477, .NET/PostgreSQL361, Worker126, tools15, reporting5, all0skips, frontend/browser/build passed.

Technical review passes do not authorize main merge or production deployment. Worktree and ignored review artifacts retained; cleanup previously received automatic policy rejection and is not retried through another tool.
