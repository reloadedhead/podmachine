# Known issues

## Some downloads permanently 403 (SABR migration) — investigated 2026-08

**Symptom**: a subset of downloads fail with `HTTP Error 403: Forbidden`,
exhaust all 3 in-cycle retry attempts (`processor.py`), and land in
`failed` — retrying minutes later doesn't help for these specific videos,
unlike the far more common case where attempt 2 or 3 succeeds.

**Root cause**: YouTube is rolling out its SABR streaming protocol, which
removes `adaptiveFormats` (the direct, fetchable URLs yt-dlp relies on)
from the player response for some clients. This is tracked upstream —
[yt-dlp#12482](https://github.com/yt-dlp/yt-dlp/issues/12482), open, with
an in-progress fix ([#13515](https://github.com/yt-dlp/yt-dlp/pull/13515)).
It's not something podmachine (or yt-dlp, yet) can work around — the
formats simply aren't in the response to fetch.

**What was tested against real failing videos, and ruled out**:

- yt-dlp was already at the latest release — no free win from updating.
- Forcing alternate `player_client` values (`android`, `tv`, `tv_simply`,
  `ios`, `web_embedded`, `web_safari`, `mweb`) — all fail identically.
- `bgutil-ytdlp-pot-provider` in script mode, using the Deno already
  bundled in the image for yt-dlp's own signature-decryption needs —
  installs cleanly, generates a valid PO token. Doesn't fix it: YouTube's
  current experiment forces a PO token specifically for `web_safari`,
  which *has* a valid token but no usable formats (SABR-only). The client
  that still has usable direct-URL formats (`android_vr`) doesn't request
  a token via this plugin at all, and its URLs 403 regardless.
- **Not tested**: cookies from a logged-in browser
  (`--cookies-from-browser`). Reasoned to be unlikely to help — this is a
  protocol-level gap (the formats aren't in the response), not an
  authentication gate a logged-in session would unlock. Also ties a real
  YouTube account to the deployment, which earlier design discussions
  explicitly avoided for privacy/simplicity. Worth revisiting only if the
  reasoning above turns out to be wrong.

**Confirmed not an IP-wide block**: an unrelated, untouched video
downloaded successfully seconds after a failing one via the plain
production code path. This is a partial, per-video/client rollout, not a
blanket block — consistent with a gradual A/B experiment.

**Current mitigation**: since the rollout is partial and evolving, a
video failing today may succeed once YouTube's experiment shifts for it
or yt-dlp ships a SABR fix. `requeue.py` automatically retries anything
stuck in `failed` once a day, capped at 5 long-range attempts, so
recovery doesn't require anyone to notice and manually reprocess.

**Revisit if**: the failure rate climbs meaningfully beyond occasional
(check `/channels`' `video_counts.failed` per channel), or once
yt-dlp#12482 ships a fix upstream (check the yt-dlp update runbook in
the README).
