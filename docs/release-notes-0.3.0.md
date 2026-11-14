# 0.3.0 optional account insights candidate

This candidate builds on the separate [0.2.0 core migration](release-notes-0.2.0.md).
It adds `birdbuddy.get_account_insights`, a response-only action with eight
explicit resources: hardware, preferences, species, named birds, weekly recap,
yearly review, feeder report and remote connection metadata.

Select an account and resource when calling the action. Ordinary feeder polling
does not request these resources. The adapter is created lazily, checks account
identity before and after reads, and uses bounded account/language-scoped caches.
Unload cancels and drains in-flight reads and clears cached data. Returned data
includes completeness, missing/truncated fields, availability and cache metadata.
See [account insights](account-insights.md) for parameters and examples.

Hardware metadata preserves owner/member/pending roles, explicit capabilities and
feature locks. Configured power profile and Smart Energy running profile remain
distinct. Camera quality and field of view are reported settings only. Named-bird
recognition state and nullable statistics remain explicit. Vendor reports retain
their authoritative periods; unavailable recaps do not become fabricated zero
counts. Remote connections remain account metadata and never create physical
devices.

Snapshot capability is preserved as an enum rather than interpreted through
string truthiness. A feeder-report server error is isolated to that request:
it is not cached, converted to zero visits, substituted with another period, or
treated as expired authentication. Regression tests cover these contracts.

There are no new cloud mutations, periodic jobs, entities or automatic collection
rules. Existing event/action compatibility aliases remain available through 0.3,
with removal planned for 0.4. Smart Energy writes, camera controls, push delivery
and camera sessions retain their [evidence gates](api-capabilities.md).

Repository validation uses the schema captured on 2026-11-13 and synthetic
fixtures. Standalone API evidence, where available, is separate from real HA
installation, lifecycle and physical-device validation. Those release gates
remain open; this is a development candidate. The manifest records the exact
immutable companion revision.
The workspace release evidence records the final test counts, archive hash and
extracted-package checks; these records do not claim production deployment.

Config-entry and registry identities and the private durable-intake store remain
unchanged. Follow the [core migration guide](automation-migration.md) when coming
from 0.1.x, and preserve the intake store across a code rollback as described in
[release validation](release-validation.md).
