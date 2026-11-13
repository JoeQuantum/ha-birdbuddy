# Push and camera research decisions

Evidence date: 2026-11-13. These decisions distinguish published schema from
working account/device behavior. No authenticated subscription, livestream,
snapshot, inference or hardware session was started for this investigation.

## Heartbeat: unresolved transport and account scope (BB-33)

The production GraphQL schema exposes `Subscription.heartbeat: AnyHeartbeat!`.
Its union has two variants:

| Variant | Selected schema fields | Missing identity needed for reliable personal visit events |
| --- | --- | --- |
| `HeartbeatPrivate` | approximate location, species | postcard ID, feeder ID, event timestamp |
| `HeartbeatPublic` | approximate location, media with species, sharing name | postcard ID, feeder ID, event timestamp |

The word heartbeat does not mean connection keepalive. Its payload can describe
wildlife content and personal location. Neither the HTTPS GraphQL URL nor the
`Private` type name establishes a subscription WebSocket endpoint, protocol,
authentication exchange, or isolation to the signed-in user's feeders.

The client and integration reviewed here implement HTTP GraphQL polling, with no
established subscription transport. The inspected schema provides no ordering,
replay cursor, reconnect guarantee, rate limit, or missed-event recovery contract.
A generic wildlife heartbeat cannot safely be treated as a new personal postcard.

**Decision:** retain polling and durable feed intake. BB-34 is deferred. A push
implementation would require a documented or captured transport, a permitted
account-only scope, sanitized event captures and bounded disconnect/reconnect tests.
If established, its first use should be a coalesced refresh hint; it must share the
existing feed checkpoint and action journal. Absence of confirmed transport is not
proof the vendor lacks one. No speculative endpoint was advertised or probed.

## Watching: schema workflow, unverified sessions (BB-35)

The schema describes `watchingWarmup`, `watchingStartV2`, `watchingStartCheck`,
`watchingActiveKeep`, `watchingActiveStop` and `watchingCooldown` mutations. The
older `watchingStart` is deprecated. Start/check results discriminate active,
failed and in-progress states. Watching itself exposes `ACTIVE`, `ENDED`, `ENDING`,
`FAILED`, `REQUESTED` and `TIMEOUT` states plus `createdByDifferentSession`.

Failures include deep sleep, firmware update, low battery, offline, off-grid,
out-of-feeder, taking postcards and unspecified failure. Those variants are
schema facts; they are not measurements of retry intervals or device behavior.

A watching object may provide `streamUrl` or `webRtcConfig`, whose fields include
`wssUrl` and ICE server URLs, username, credential and TTL. This is a session-based
cloud workflow; it does not establish RTSP, a permanent stream URL, a known
signaling protocol or a local reboot endpoint. Do not persist ICE credentials or
signed stream URLs in recorder events or diagnostic exports.

Snapshot operations have separate side effects. A server snapshot can return a
temporary image URL. The schema's WebRTC snapshot-identification operation accepts
uploaded snapshot bucket keys and performs inference. A picture refresh cannot
silently invoke it.

[Client livestream PR 28](https://github.com/jhansche/pybirdbuddy/pull/28) includes
an experimental workflow and a maintainer warning about interrupting bird detection.
[Client issue 55](https://github.com/jhansche/pybirdbuddy/issues/55) reports that cloud
access is needed to initiate streaming and no local device API is known. These
reports motivate validation; they do not validate current hardware in this fork.
The only feature-lock enum observed is `LIVESTREAM`; it must not gate unrelated
power, audio or ordinary postcard reads.

**Decision:** BB-35 runtime validation is blocked on an explicitly selected,
authorized feeder and an account session. BB-36 is deferred. No camera entity or
session mutation is shipped. Required evidence before proceeding: supported role
and hardware, entitlement/capability response, bounded start/keep/stop/cooldown,
stream protocol and ICE expiry, simultaneous app-session handling, cancellation and
unload cleanup, and measured effects on postcard capture. Every attempted session
must have a verified stop path. No reliable cleanup or interruption measurement is
claimed from offline fixtures.

## Camera controls (BB-27)

`CLOSE_UP`/`WIDE` and video-quality enums are schema choices, not per-device support
lists. The schema has start/check result unions, but no authorized hardware result
has established the available choices here. Keep camera quality/FOV controls
unexposed until that evidence exists. Read-only reported values can be represented
without turning every enum into a selectable setting.

## Reproducible source record

The rich unauthenticated introspection completed at 2026-11-13T19:09:10Z, HTTP 200,
with 568 types and 167 root operations (51 queries, 115 mutations, 1 subscription).
The full schema and provenance are retained in the companion client's
`tests/fixtures/schema.json` and `schema-provenance.json`. The review workspace also
retains the catalog, operation inventory, schema SHA256, probe classifications and
all issue/PR coverage mappings. Unit tests use those stored fixtures and do not
contact the live service. Refresh introspection deliberately during a future
release review; never make regular integration polling or CI depend on it.
