# Model network boundary decisions — 2026-10-20

Plan: `docs/superpowers/plans/2026-10-20-model-network-boundary.md`.

- Task1: Ruling: retain arbitrary public HTTPS443 compatible hosts rather than force fixed vendor allowlist — product has custom endpoint settings, user has authorized runtime hardening, no approved vendor-only scope — cost: deployment should add outbound network policy where available; code validates every actual connection. Only safe simple ASCII API prefix/full chat path is supported by legacy adapter, no escaped/dot/control/duplicate-slash path; cost: unusual encoded endpoints need deliberate future support.

- Task1: Ruling: reproduced native NAT64-to-private acceptance and .NET ambiguous URL/transition/benchmark/documentation IP gaps are same BUG014 network boundary, so tighten native shared standard-library policy and .NET configuration/DNS check after RED, keeping their existing pinned clients — cost: encoded/relative paths and transition networks require deliberate future support. IANA global exceptions under2001::/23 remain allowed, no blanket denial of all special-purpose public allocations. Sources verified official IANA IPv4/IPv6 registries and Python3.12 ssl/socket/ipaddress docs.

Final review: Critical0, Important0, Minor0; no declined-to-judge items or deferred minors. Fresh reviewer independently ran15/15 offline network tests. Exact final reviewed commit `b3d54e63db77f91de6c1b09741db7948b9b69089`: [Linux39425425785](https://github.com/Lemondick/quant-to-riches/actions/runs/39425425785), Python492/.NETPG385/Worker128/tools15/report5 all0skips; front/browser/build pass. Review does not authorize mainmerge/production deployment.

Ignored review workspaces retained; earlier automatic policy rejection of cleanup is respected without retrying deletion through another tool.
