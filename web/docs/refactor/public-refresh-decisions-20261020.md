# 阶段 D 实现取舍与审查记录

- Task 1: Ruling: explicit refresh signals coalesce until the completion cooldown elapses, including outside trading hours — prevents manual requests bypassing failure backoff — cost: manual refresh can wait up to one configured interval.

- Task 1: Ruling: persisted public_compute_snapshots is also vulnerable to empty publication (SQL trigger 0022 accepts empty arrays); add a narrowly scoped public cache BEFORE trigger via migration0023 after its PG RED test — preserves prior public values/clocks across restart without rewriting financial history — cost: one additive migration and documentation latest-number change.

- Final review: fresh reviewer found no Critical/Important/Minor; review did not run tests. Final: Ruling: preserve pre-existing shared compute job deadlines instead of shortening another callers lifetime — own wait is canceled and newly created tasks are bounded, identical public work stays single-flight — cost: reused shared work may outlive this cycles budget until its original deadline. Linux result itself is resolved by actual CI gate, not review opinion. b276 CI361 .NET360pass1test-helper duplicate-config failure; Python446 445pass1obsolete Warm-method substring failure. Both defects in test setup/lexical assertion identified from logs; fix helper override and replace obsolete method-name assertion with existing actual PG coverage, no production assertions weakened.

独立审查无 Critical、Important、Minor。Linux39390147290 全部通过、零跳过。原工作区、main、生产和财务历史未修改。临时审查目录保留，因先前自动审批拒绝清理，未用其他工具重试删除。
