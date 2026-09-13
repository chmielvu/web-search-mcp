# AGENTS.md — youtube package

YouTube transcript, search, channel, and analysis subsystem. The transcript
cascade lives in `cascade.py`; audio-sourcing ASR backends in `whisper.py`;
legacy subtitle extraction in `yt_dlp_backend.py`; Data API v3 wrappers in
`api_*.py` / `channel_api.py`.

## Transcript cascade

`cascade.fetch_transcript_cascade` returns `(segments, backend_used)` and
validates `backend` against `_VALID_BACKENDS = ("auto", "ytdlp", "cf_whisper",
"whisper", "api")`. Layer order in `auto`: yt-dlp subtitles → Cloudflare
Workers AI Whisper (`cf_whisper`, gated on `CLOUDFLARE_ACCOUNT_ID` +
`CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_API_KEY`) → Whisper Space
(`whisper`, auto only, gated on `WHISPER_SPACE_ID`/`WHISPER_SPACE_URL`) →
legacy `youtube-transcript-api` (`api`). Failures accumulate into the
`TranscriptBackendError` message.
Known limitation: on a datacenter VPS, YouTube returns `error.api.youtube.login`
regardless of session tokens. Verified live (2026-09-12): with a freshly
minted bgutil poToken, the raw `youtubei/v1/player` probe returns
`LOGIN_REQUIRED` for WEB, MWEB, and TVHTML5 clients, and
`WEB_EMBEDDED_PLAYER` is deprecated (`ERROR: video unavailable`).
cobalt additionally gates session-token use behind `quality > 1080`
(`youtube.js` `useSession`), and audio requests force `quality=1080` in
`match.js`, so audio never uses the session server on stock images.

Verified-live unblock outcomes (2026-09-12): YouTube cookies
(`cookies.json` → `COOKIE_PATH` on cobalt) load correctly and remove the
login error, but requests from a datacenter IP are still challenged —
WEB client + full auth cookies + freshly minted poToken + visitorData all
combined still return `LOGIN_REQUIRED`. Account sessions appearing from a
datacenter IP are flagged regardless of tokens. Remaining fixes require
egress change:
  - Residential/mobile proxy: `HTTPS_PROXY` on cobalt AND a per-request
    `proxy` for bgutil `/get_pot` minting (tokens and streams must share
    the egress).
  - Run the cobalt stack on a residential machine instead.
Cookies remain required either way: they carry the account session.

VPS session stack (deployed 2026-09-12): `bgutil-provider`
(brainicism/bgutil-ytdlp-pot-provider) behind `session-adapter`, a
Content-Type fix-up sidecar, because cobalt POSTs `/get_pot` without a
Content-Type header and bgutil 2.x answers 415 to that. yt-session-generator
is incompatible with cobalt 11.7.1 by design (it only serves `GET /token`,
cobalt expects `POST /get_pot`).

Audio-sourcing behavior: `CobaltAudioError` falls back to yt-dlp with a
warning log — cobalt is an optimization, never a hard dependency. The
local ISP CDN edge caps each YouTube media stream at 1 MiB (verified
2026-09-12): ranges beyond offset 1 MiB return HTTP 403 regardless of
itag, client, or fresh URL; keep this in mind when a local download
"works" for short videos only.

Non-YouTube cobalt services (e.g. SoundCloud) work end-to-end through the
same client.

## Whisper Space tier (stub)

`fetch_hf_space_transcript_sync` is env-gated: `WHISPER_SPACE_ID` routes
through `gradio_client` (auto-detects URL-parameter endpoints);
`WHISPER_SPACE_URL` posts to a self-hosted `/api/predict`. Unconfigured →
`WhisperClientError`, cascade falls through. Public Spaces are best-effort:
YouTube bot-blocks their downloaders server-side.
