# Episode retention

Downloaded episodes accumulate indefinitely today — nothing ever removes
an mp3 once it's downloaded. On a Pi with limited (and possibly SD-card)
storage, that's a real problem for anyone following several active
channels. This document plans three retention strategies and the shared
mechanics they all need. **Only the count strategy is implemented.** Age
and size are designed here so they can be built later without
re-deriving the approach.

## The one constraint every strategy must respect

"Delete" can never mean removing the row from the `videos` table.

The poller dedupes against every video it's ever seen for a channel:

```sql
SELECT video_id FROM videos WHERE channel_slug = ?
```

This query is status-agnostic by design — it's what makes "no history
backfill" work (`poller.py`). If a retention pass deleted the *row*
instead of just the file, the next poll would see that video's ID as
unrecognized, treat it as newly discovered, and queue it right back up
for download. Retention would fight the downloader forever.

So deletion means: remove the mp3 from disk, and update the row to a
terminal `deleted` status with `file_path`/`file_size` cleared and
`deleted_at` set. The row stays forever as a tombstone. Two things fall
out of this for free:

- `feed.py`'s existing episode query (`WHERE status = 'done'`) already
  excludes `deleted` episodes — no feed-generation changes needed.
- `/channels`' `video_counts` (grouped by status) will show a `deleted`
  bucket automatically.

## Where it runs

As a step in `run_cycle` (`scheduler.py`), once per channel, after
`process_pending_videos` — same place `ensure_channel_artwork` was
added. Cheap to check even when nothing needs deleting.

## Config shape

A global default in `AppConfig`, optional per-channel override in
`ChannelConfig`. A channel's `retention:` block fully replaces the
global one for that channel (no field-by-field merging — simpler to
reason about than partial overrides).

```yaml
# global default
retention:
  strategy: count       # none | count | age | size
  keep_latest: 20

channels:
  - id: UCxxxxxxxxxxxxxxxxxxxxxx
    name: Example Channel
    slug: example-channel
    retention:            # optional, replaces the global default for this channel
      strategy: count
      keep_latest: 5
```

Default strategy is `none` (retention off). Auto-deleting files is
destructive and irreversible enough that it should be opt-in, not a
silent new behavior after an upgrade.

## Strategy 1: count (implemented)

Keep the `keep_latest` most recent `done` episodes per channel; delete
the rest.

- **Config**: `keep_latest: <int>` (required, must be ≥ 1).
- **Algorithm**: query `done` episodes for the channel ordered by
  `published_at DESC`; anything beyond position `keep_latest` gets
  deleted.
- No separate safety floor needed — `keep_latest` *is* the floor, by
  construction.
- Simplest strategy to reason about ("I keep my last 20") but doesn't
  know or care about actual disk usage — 20 episodes could be 200MB or
  20GB depending on length.

## Strategy 2: age (planned)

Delete episodes older than `max_age_days`, but never below a safety
floor of the most recent `keep_latest_minimum` episodes — otherwise a
low-frequency channel with a large `max_age_days` gap could get wiped
to zero in one pass.

- **Config**: `max_age_days: <int>` (required), `keep_latest_minimum:
  <int>` (default e.g. 3).
- **Algorithm**: query `done` episodes ordered by `published_at DESC`.
  Keep the first `keep_latest_minimum` unconditionally. For the rest,
  delete any with `published_at` older than `now - max_age_days`.
- Matches "podcasts are about recent content" intuitively, but a
  prolific channel could still blow past a disk budget before anything
  ages out — this strategy doesn't bound total size either.

## Strategy 3: size (planned)

Cap total storage per channel; delete oldest-first once over budget.
Most directly answers "these pile up," but the most work to build.

- **Config**: `max_total_mb: <int>` (required), `keep_latest_minimum:
  <int>` (default e.g. 3, same reasoning as age).
- **Algorithm**: query `done` episodes ordered by `published_at DESC`
  with `file_size`. Keep the first `keep_latest_minimum` unconditionally.
  Walk the rest newest-first, accumulating a running total; once the
  running total would exceed `max_total_mb`, delete that episode and
  everything older.
- Needs no new column — `file_size` is already recorded on `done`
  episodes.
- Global vs. per-channel budget is a real open question if this is
  built: a single global `max_total_mb` is simpler, but a channel that
  posts long-form video would starve out shorter channels sharing the
  same budget. Per-channel (as specified above) avoids that at the cost
  of the user having to think about each channel's budget individually.

## Testing shape (for whichever strategy is under work)

- Deletes the right episodes, keeps the right ones, at the boundary
  (exactly `keep_latest`, exactly at the age cutoff, exactly at the
  size budget).
- No-ops when nothing exceeds the policy.
- File already missing on disk doesn't raise (idempotent).
- **The regression test that matters most**: a deleted episode's
  `video_id` must not be rediscovered as `pending` by `poll_channel` on
  the next poll. This is the entire reason for tombstoning instead of
  row deletion — a strategy that skips this test could look correct and
  still create a redownload loop in production.
- A deleted episode is excluded from `build_channel_feed`'s output.
