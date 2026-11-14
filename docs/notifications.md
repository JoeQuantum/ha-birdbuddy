# Visitor notifications

The [notification automation](../examples/notify_postcard.yaml) handles
recognized, unidentified, image-only and video-only visits. Replace the account,
feeder and `notify.mobile_app_your_phone` placeholders before enabling it.
The example does not identify, collect or share a postcard.

It listens only for version 1 live events for the chosen account and feeder.
Before building a notification it calls `birdbuddy.get_postcard_details` with
the original IDs. That read refreshes selected URLs and retrieves details
omitted from large events. If the read fails, the run fails visibly in the
automation trace; it does not send a misleading empty result or retry a cloud
mutation. Later queued runs can continue.

Names come only from recognized `species`, not suggestions or confidence
scores. An empty list becomes **Unidentified visitor**. Multiple species names
are joined. The message counts every image and video. A video's thumbnail may
be used as the preview image; its video content URL is never passed as an
image. If no preview exists, the image field is omitted and the text still
shows the video count. Notification-client support for images varies.

The stable event ID is passed as the notification `tag`, allowing clients that
support replacement tags to replace repeated notifications for the same visit.
This is not an exactly-once guarantee or a durable record of external effects.
Event delivery can repeat after a crash. Consumers that require strict
application-level deduplication must persist event IDs, rather than media URLs.

The queue is finite: 20 running/queued actions, with warning-level overflow.
Queued runs are not durable across automation reloads or restarts. Review traces
and logs if notifications are missing; increasing the queue is not a persistence
mechanism. Historical `birdbuddy_postcard_backfill` events are intentionally
excluded. See [durable intake](durable-intake.md) for integration delivery limits
and [local archiving](local-archive.md) for storing all media separately.
