# podmachine

Follows YouTube channels, downloads new uploads as audio, tags them, and serves
a podcast RSS feed per channel to any podcast app on your local network.

Runs as a single Docker container, intended for a Raspberry Pi.

## Setup

1. Get the compose file and config:

   ```bash
   mkdir podmachine && cd podmachine
   curl -fsSL -o docker-compose.yml https://raw.githubusercontent.com/reloadedhead/podmachine/main/docker-compose.yml
   mkdir -p config
   curl -fsSL -o config/config.yaml https://raw.githubusercontent.com/reloadedhead/podmachine/main/config/config.example.yaml
   ```

2. Edit `config/config.yaml`: set `base_url` to an address your podcast
   apps can reach on your LAN (e.g. `http://podmachine.local:8000` or
   `http://<pi-ip>:8000`), and list the channels you want to follow.

3. Pull and run:

   ```bash
   docker compose pull
   docker compose up -d
   ```

That's it — podmachine polls and downloads automatically from here on
(`poll_interval_minutes`, starting immediately). Point a podcast app at:

```
http://<pi-ip-or-podmachine.local>:8000/feeds/<channel-slug>.xml
```

To update later, re-run step 3.

## Published image

Every push to `main` that touches `src/`, `Dockerfile`, or
`requirements.txt` builds and publishes a multi-arch (amd64 + arm64)
image via GitHub Actions to `ghcr.io/reloadedhead/podmachine:latest`
(`.github/workflows/docker-publish.yml`) — public, no login needed to
pull.

## Config reference

| Field | Description |
|---|---|
| `poll_interval_minutes` | How often to check channels for new uploads |
| `base_url` | LAN-reachable base URL used in generated feed/enclosure links |
| `channels[].id` | YouTube channel ID (`UC...`) |
| `channels[].name` | Display name used as podcast/episode metadata |
| `channels[].slug` | URL-safe identifier, used in feed and file paths |

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /healthz` | Liveness check |
| `GET /channels` | Per-channel status: baseline state, poll failures, video counts |
| `POST /run` | Force a full poll + download cycle immediately |
| `POST /poll` | Poll for new videos only, without downloading |
| `POST /process` | Download/tag anything already pending, without polling |
| `GET /feeds/<slug>.xml` | Podcast RSS feed for a channel |
| `GET /media/<slug>/<file>` | Episode audio file |
| `GET /artwork/<slug>.jpg` | Channel cover art |

## Development

Run tests inside a container matching the production Python version (the
host machine's Python may be newer than what pinned deps have wheels for):

```bash
docker run --rm -v "$(pwd)":/app -w /app python:3.12-slim-bookworm \
  bash -c "pip install -r requirements-dev.txt && pytest -q"
```

## Resilience & hardening

- **Retry with backoff.** A failed download is retried up to 3 times (2s,
  4s backoff) before being marked `failed`. YouTube 403s are often
  transient — this was verified directly: a manual retry during testing
  succeeded immediately, and the automated retry logs the same recovery
  pattern in production use.
- **Per-channel circuit breaker.** A channel that fails to poll 5 times in
  a row (bad channel ID, deleted channel, persistent network issue) is left
  alone for 2 hours instead of being retried every cycle. Resets
  automatically on the next successful poll. Visible in `/channels` via
  `consecutive_poll_failures`, `backed_off_until`, and `last_poll_error`.
- **Politeness.** Channels are polled sequentially with a randomized 1-4s
  gap between them, not in parallel; downloads use a similar gap plus a
  ~2MB/s rate limit. None of this is about bandwidth — it's about not
  looking like a script hammering YouTube as fast as possible.
- **Non-root, read-only container.** The container runs as an unprivileged
  user (uid 1000) with a read-only root filesystem — only `/data` and
  `/tmp` (tmpfs) are writable. Verified end-to-end under both restrictions:
  real downloads, tagging, and feed/media serving all still work; writing
  anywhere else in the container fails as intended.
- Only videos published *after* a channel is added are downloaded — no
  history backfill.
- YouTube Shorts are skipped, detected directly from the RSS entry's link
  (`/shorts/...` vs `/watch?v=...`) — no extra request needed.
- The Docker image bundles Deno as yt-dlp's JS runtime, required for
  YouTube's signature extraction — without it, downloads fail with a
  misleading HTTP 403.
- **Feed artwork.** Each channel's real avatar is fetched once (via a cheap
  yt-dlp metadata-only call that doesn't enumerate any videos), cached to
  `/data/artwork/<slug>.jpg`, and served at `/artwork/<slug>.jpg` for use
  as the podcast's cover art — not hotlinked from Google's CDN. Until it's
  fetched (or if the fetch ever fails), the feed falls back to borrowing
  the most recent episode's thumbnail so it's never imageless. Per-episode
  artwork always uses that episode's own real thumbnail.
- The iTunes category is hardcoded to "Society & Culture" (Apple's taxonomy
  has no generic "Other") for every channel — not yet configurable per
  channel.
- For personal/private LAN use only — don't expose this outside your network
  or redistribute the feeds.

## yt-dlp update runbook

`yt-dlp` is pinned to a specific tested version in `requirements.txt`
because YouTube changes frequently enough that an untested update can
silently break downloads. Signs you need to bump it:

- Downloads that used to work now fail consistently (not just an
  occasional 403 that clears on retry — see the retry/backoff logs)
- `/channels` shows a channel's episodes stuck in `failed` across multiple
  cycles

To update:

```bash
# find the latest version
docker run --rm python:3.12-slim-bookworm bash -c \
  "pip install --quiet yt-dlp && python3 -c 'import yt_dlp; print(yt_dlp.version.__version__)'"
```

Edit the `yt-dlp==...` line in `requirements.txt` to that version, then
rebuild (`docker compose up --build -d`) and check `/channels` after the
next cycle. If it doesn't help, it's usually a matter of days before
yt-dlp ships a fix — try again then.
