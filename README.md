# podmachine

Follows YouTube channels, downloads new uploads as audio, tags them, and serves
a podcast RSS feed per channel to any podcast app on your local network.

Runs as a single Docker container, intended for a Raspberry Pi.

## Status

Phase 1 — channel polling is live: new uploads are detected via each
channel's YouTube RSS feed and tracked in SQLite. Downloading, tagging, and
feed serving land in later phases.

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

4. Trigger a poll and inspect channel state (this happens on a schedule
   automatically starting in Phase 4 — for now it's manual):

   ```bash
   curl -X POST http://localhost:8000/poll
   curl http://localhost:8000/channels
   ```

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
- For personal/private LAN use only — don't expose this outside your network
  or redistribute the feeds.
