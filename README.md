# podmachine

Follows YouTube channels, downloads new uploads as audio, tags them, and serves
a podcast RSS feed per channel to any podcast app on your local network.

Runs as a single Docker container, intended for a Raspberry Pi.

## Status

Phase 2 — the full pipeline works: new uploads are detected via RSS,
downloaded as audio-only with yt-dlp, and tagged with mutagen (title,
channel as artist/album, date, embedded cover art, description). Feed
generation and serving land in a later phase; everything is still manually
triggered via HTTP endpoints until Phase 4 adds scheduling.

When a channel is polled for the first time, its current catalog is recorded
as a baseline and nothing is queued for download — only videos discovered on
*later* polls are treated as new. This is what enforces "no history
backfill" even across container restarts (state lives in the `/data` volume).

## Setup

1. Copy the example config and edit it:

   ```bash
   cp config/config.example.yaml config/config.yaml
   ```

   Set `base_url` to an address your podcast apps can reach on your LAN
   (e.g. `http://podmachine.local:8000` or `http://<pi-ip>:8000`), and list
   the channels you want to follow.

2. Build and run:

   ```bash
   docker compose up --build -d
   ```

3. Check it's alive:

   ```bash
   curl http://localhost:8000/healthz
   ```

4. Trigger a poll, then download+tag anything newly discovered (this
   happens on a schedule automatically starting in Phase 4 — for now both
   steps are manual):

   ```bash
   curl -X POST http://localhost:8000/poll
   curl -X POST http://localhost:8000/process
   curl http://localhost:8000/channels
   ```

   Downloaded episodes land in the `podmachine_data` volume under
   `media/<channel-slug>/<video-id>.mp3`.

## Config reference

| Field | Description |
|---|---|
| `poll_interval_minutes` | How often to check channels for new uploads |
| `base_url` | LAN-reachable base URL used in generated feed/enclosure links |
| `channels[].id` | YouTube channel ID (`UC...`) |
| `channels[].name` | Display name used as podcast/episode metadata |
| `channels[].slug` | URL-safe identifier, used in feed and file paths |

## Development

Run tests inside a container matching the production Python version (the
host machine's Python may be newer than what pinned deps have wheels for):

```bash
docker run --rm -v "$(pwd)":/app -w /app python:3.12-slim-bookworm \
  bash -c "pip install -r requirements-dev.txt && pytest -q"
```

## Notes

- Only videos published *after* a channel is added are downloaded — no
  history backfill.
- YouTube Shorts are skipped, detected directly from the RSS entry's link
  (`/shorts/...` vs `/watch?v=...`) — no extra request needed.
- The Docker image bundles Deno as yt-dlp's JS runtime, required for
  YouTube's signature extraction — without it, downloads fail with a
  misleading HTTP 403. Occasional 403s even with Deno present are normal
  YouTube-side flakiness; Phase 5 adds retry/backoff for this. There's no
  retry yet — a failed download is marked `failed` in the database and
  `/process` won't touch it again automatically.
- For personal/private LAN use only — don't expose this outside your network
  or redistribute the feeds.
