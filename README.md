# podmachine

Follows YouTube channels, downloads new uploads as audio, tags them, and serves
a podcast RSS feed per channel to any podcast app on your local network.

Runs as a single Docker container, intended for a Raspberry Pi.

## Status

Phase 0 (scaffold) — config loading and a `/healthz` endpoint only. Channel
polling, downloading, tagging, and feed serving land in later phases.

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

## Config reference

| Field | Description |
|---|---|
| `poll_interval_minutes` | How often to check channels for new uploads |
| `base_url` | LAN-reachable base URL used in generated feed/enclosure links |
| `channels[].id` | YouTube channel ID (`UC...`) |
| `channels[].name` | Display name used as podcast/episode metadata |
| `channels[].slug` | URL-safe identifier, used in feed and file paths |

## Notes

- Only videos published *after* a channel is added are downloaded — no
  history backfill.
- YouTube Shorts are skipped (duration ≤ 60s).
- For personal/private LAN use only — don't expose this outside your network
  or redistribute the feeds.
