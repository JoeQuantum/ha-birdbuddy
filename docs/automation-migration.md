# Migrating postcard collection automations

The current collection workflow uses `birdbuddy_new_postcard` events and an
explicit `selection_policy: server`. It does not reproduce the old
`recognized`, `best_guess`, `mystery`, or numeric confidence policies. Review
that difference before enabling automatic collection.

## Upgrade an installed blueprint

1. Disable automations created from the old collection blueprint. Save a copy
   of their YAML so you can inspect the previous choices.
2. Re-import `custom_components/birdbuddy/blueprints/collect_postcard.yaml` from
   this repository through Home Assistant's blueprint interface. Updating the
   integration does not update a downloaded blueprint copy.
3. Create a new automation from the updated blueprint, or deliberately replace
   the old blueprint inputs. Select both the Bird Buddy account and feeder.
4. Review the new collection policy. The default, **Disabled — review the policy
   first**, makes no collection calls. Choose **Use Bird Buddy's server
   selection** only if that is the behavior you want.
5. Choose whether to identify first and request sharing. Both default to false;
   the old blueprint's `share_media: true` default is not carried forward.
6. Choose a finite queue limit, save the automation, and inspect its trace after
   a new live postcard arrives. Keep the previous automation disabled so both
   copies do not process the same feed.

Remove the old `collect_strategy`, `best_guess_confidence`, and `share_media`
blueprint inputs. They are not translated into a new policy. The service also
rejects legacy `strategy`, confidence and `sighting` fields rather than silently
changing their meaning. Equivalent service aliases for `postcard.id` and
`share_media` have a limited compatibility window; new automations should use
`postcard_id` and `share` directly.

## Queue behavior

The blueprint uses `mode: queued` with a default maximum of 20 total running and
waiting executions. Calls run in order. A failed or cancelled individual call
releases its queue slot, allowing later work to proceed.

When the limit is reached, Home Assistant logs a warning. The excess event is
not automatically retried. A trace records ordinary action failures and the
selected inputs. Review the postcard through the read-only details service
before retrying a failed or uncertain collection: the server may already have
completed it.

The automation queue is held in memory. Disabling/reloading the automation or
restarting Home Assistant can cancel waiting work. Increasing the limit does
not make that queue durable. Review your traces and recent postcards after a
restart or overflow rather than replaying collection mutations blindly.

The blueprint only accepts schema version 1, `source: live`, new-postcard events
for the selected account and feeder. Historical/backfill and collected-postcard
events do not trigger collection. Missing identity fields also prevent a call.
The original stable `event_id` remains available as
`trigger.event.data.event_id`, and is copied to `birdbuddy_event_id` in the run's
variables for tracing or custom deduplication. It is not an extra collection
service argument.

## Service contract

A collection call identifies a postcard and explicitly chooses server selection:

```yaml
service: birdbuddy.collect_postcard
data:
  config_entry_id: "YOUR_BIRD_BUDDY_CONFIG_ENTRY"
  postcard_id: "POSTCARD_ID"
  selection_policy: server
  identify: false
  share: false
```

The blueprint also forwards the event's `account_id` and `feeder_id`, plus the
selected `device_id`. All supplied targets must agree. This prevents a shared
feeder or several configured accounts from silently selecting the wrong client.

`identify: true` explicitly runs inference before collection. `share: true`
requests sharing; a sharing request is not proof of public visibility. These
choices can change server state and are never enabled by this migration itself.
No automation is installed, enabled or changed on your Home Assistant instance
by the repository's tests or development work.
