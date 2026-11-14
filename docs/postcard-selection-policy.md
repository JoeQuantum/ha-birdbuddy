# Postcard collection policy

Decision: modern collection requires the explicit service input
`selection_policy: server`. Legacy `recognized`, `best_guess`, `mystery`, and
numeric confidence rules have no validated equivalent and are rejected before
inference, collection, or sharing. Omitting a legacy option does not silently
select a replacement policy: callers must opt into `server` explicitly.

This decision is based on the schema captured on 2026-11-13 and the public
upstream client fixtures. No authenticated experiment or postcard mutation was
performed. A valid GraphQL document establishes field compatibility, not
account permissions, retention behavior, or a successful operation.

## Evidence and limits

The current schema exposes `postcardMediasDetails(feedItemId)` and per-image
`inferenceMediaDecision`. Recognized images contain a species list and
suggestions; cannot-decide and not-recognized images contain suggestions.
Each suggestion has `confidence: Int!`. The schema supplies no confidence
unit, range, threshold, or tie-breaking rule. It does not establish that these
integers use the old report-token percentage scale.

Video details have their own species and suggestion lists, rather than the
image decision union. A not-recognized decision does not prove that there is
no bird. An empty species list does not establish how the server will retain
that image or video when collected.

`PostcardCollectInput.mediaSpeciesItems` is optional. Each item requires a
media ID and a species-ID list, whose list may be empty according to GraphQL
typing. The schema does not specify what omission, null, an empty outer list,
an empty species list, or a subset of the card's media means. None of those
forms is a proven "keep as mystery" or "discard" operation.

The public modern fixture records `LOW_CONFIDENCE` and `HIGH_CONFIDENCE` on
postcards, recognized species in the preview, and media IDs after collection.
It does not contain modern numeric per-media decision results or an explicit
`mediaSpeciesItems` selection. Historical `issue-40.json` contains decoded
legacy token confidences; those are evidence for the old implementation only.
The sanitized modern examples are in `tests/fixtures/data/public_*.json`.

These distinctions matter because upstream [issue 17](https://github.com/jhansche/ha-birdbuddy/issues/17)
documents media loss when a previous best-guess implementation failed to
preserve each sighting's selection. [Issue 40](https://github.com/jhansche/ha-birdbuddy/issues/40)
proposes anomaly correction across media but acknowledges uncertainty. Neither
is evidence that the modern API supports an equivalent safe mapping.

The research query in [postcard-media-policy.graphql](queries/postcard-media-policy.graphql)
is read-only and was validated offline against the captured schema. It is not
called by polling or collection. A future supported custom policy needs
sanitized decision responses and separately authorized tests of a specifically
selected postcard, with before/after asset counts and IDs. Until then, the
integration does not send `mediaSpeciesItems` or attempt anomaly correction.

## Service contract

<!-- markdownlint-disable MD013 -->

| Input | Result |
| --- | --- |
| `postcard_id` plus `selection_policy: server` | Use the modern server-selected collection path. |
| Missing/null/unknown `selection_policy` | Reject with migration instructions. |
| Any explicit `strategy`, including `recognized` or null | Reject before side effects. |
| Any `best_guess_confidence` or `confidence_threshold`, including null | Reject before side effects. |
| Legacy `sighting`/report data or explicit media/species selection | Reject before side effects. |
| `postcard: {id: ...}` | Temporary ID alias; conflicting IDs are rejected. Other envelope metadata does not control collection. |
| Boolean `share_media` | Temporary alias for the modern `share` intent. Conflicting aliases are rejected. |
| Absent sharing option | `share: false`. |
| Absent `identify` | `identify: false`; collection does not implicitly run inference. |
| `identify: true` | Explicitly request inference before collection; subject to bounded operation handling. |
| String/numeric sharing value | Reject; `"false"` must never become truthy sharing. |
| Conflicting or ambiguous account/device/feeder targets | Reject before invoking the client. |

<!-- markdownlint-enable MD013 -->

The sharing alias maps the user's requested sharing intent to the modern
`share` input. It does not claim that the server's media-selection behavior
matches the old per-sighting path, and it does not call a second sharing
mutation. The server may classify or retain media differently from the old
strategies; `server` is deliberately a new policy name.

Migrated service data has this form:

```yaml
postcard_id: "the-selected-postcard-id"
config_entry_id: "the-selected-account-entry-id"
selection_policy: server
share: false
identify: false
```

Validation is pure and does not fetch or collect anything. The service must
validate all inputs and resolve the account before invoking inference or
collection. A failed migration never triggers historical collection. Existing
automations are not rewritten automatically.
