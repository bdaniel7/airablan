# airable-api

A local, self-hosted fallback for the Yamaha's internet radio (airable) service.
Runs on a Raspberry Pi (or any always-on Linux box). It learns the real airable API by
sitting in front of it, and can keep your radio working when airable goes away.

## How it works

Your Yamaha streamer fetches its `NET RADIO` station list over HTTPS from `3573112786.airable.io`
(a per-device airable endpoint), then streams audio directly from each broadcaster.

This project points that host at your Pi and does three things:

1. **Capture** — every request/response is decrypted by your Pi and written verbatim to
   `data/captures/` so we can discover the exact API schema.
2. **Relay + cache** — requests are forwarded live to the real airable endpoint and the
   answers are cached on disk (`data/cache/`).
3. **Fallback** — if the real airable endpoint ever stops answering, cached responses are
   served automatically (`cache.stale_on_error: true`), keeping the radio list working.

Phase 3 (after the capture phase reveals the schema) adds fully offline menus with your own
stations from `stations.yaml` and optional RadioBrowser aggregation.

> **Risk to confirm first:** this only works if the streamer does not strictly validate the
> TLS certificate on `*.airable.io`, like Frontier Silicon radios. This repo terminates TLS
> with a self-signed certificate. If the receiver rejects it, you'll see TLS handshake
> errors in `data/handshake.log` and no HTTP requests in `data/captures/` — in that case
> self-hosting the list isn't possible on this hardware.

## Status: schema captured, offline mode implemented

The capture phase worked — the streamer accepted the self-signed certificate, and the API
contract is now known:

- `GET /authentication?device=<mac>&name=WN9711&version=1.0.0&locale=...&salt=...&signature=<md5>`
  → `{ "id": [...], "token": "<token>", "time": <unix> }`
- Subsequent requests carry `Authorization: Basic <token>:<md5>` and a session cookie
- `GET /` → `{ "id": ["airable","listing","index"], ... "content": { "entries": [...] } }`
  (a generic directory-tree API — every menu/catalog is just JSON entries pointing at URLs)
- `GET /id/airable/radio/<id>` → station record (title, slogan, images, `streams[]`)
- `GET /airable/play/radio/<id>/<streamid>` → `{ "id":["airable","redirect",...], "url":"<real stream URL>", ... }`
   the device then connects straight to the stream URL (audio is not proxied)

Offline mode (`mode: offline`) implements this contract from `stations.yaml`, so the radio
keeps working even if airable dies — including your existing stored favorites, because the
sample file reuses your real station IDs.

## Offline mode

1. Edit `stations.yaml` — folders with stations:
   ```yaml
   My Favorites:
     Magic FM:
       id: "8209371091797949"      # optional; keep your airable ids so stored favorites still work
       url: "http://live.magicfm.ro/magicfm.aacp"
       codec: AAC                  # optional, default MP3
       bitrate: 80
     Any New Station:
       url: "http://my.server/stream.mp3"
   My Second Folder:
     ...
   ```
2. Set `mode: offline` in `config.yaml` and restart the proxy. No upstream/internet needed.
3. On the receiver: select `NET RADIO`. The menu is now **your** folders → stations.
4. To switch back to live airable, set `mode: proxy` and restart.

Notes:
- The streamer (per airable/Yamaha) cannot play HLS or DASH — provide plain HTTP MP3/AAC
  streams (Icecast-compatible). HTTPS streams are not supported by the device either.
- Stations without an explicit `id` get a stable ID derived from their stream URL, so
  favorites made against your server persist across restarts.
- `static.airable.io` logo URLs are not reproduced (offline icons are blank); set `images: []`
  is handled automatically.

## Requirements

- Raspberry Pi (any 64-bit OS) with a static LAN IP
- Receiver and Pi on the same reachable network
- Ports `443/tcp` (required), and optionally `53/udp + 53/tcp` for the built-in DNS
- `openssl` installed (usually already present)
- Docker + docker compose, or Python 3.11+ for running without Docker

## Quick start (on the Pi)

```bash
# 1. get the code and set up the config
git clone <your-repo> airable-api
cd airable-api
cp config.yaml config.local.yaml   # edit if you want the optional DNS server

# 2. create a local CA once, then sign the radio API host cert with it
./scripts/gen_ca.sh
./scripts/gen_cert.sh 3573112786.airable.io

#    (optional) also mirror station logos:
# ./scripts/gen_cert.sh static.airable.io
#    then uncomment the static.airable.io block in config.local.yaml
#    afterwards, install ssl/ca.crt on your phone (see "Phone / MusicCast app trust")

# 3. run it
docker compose -f docker-compose.yml up -d --build
#    or, without Docker:
# python3 -m venv .venv && . .venv/bin/activate
# pip install -r requirements.txt
# python -m app.proxy --config config.local.yaml
```

Open `config.local.yaml`: set the Pi's LAN IP under `dns.override` only if you plan to use
the built-in DNS (leave `dns.enabled: false` if you'll use router CNAMEs instead).

## Point the radio at the Pi

Choose one:

**Option A — CNAME on your local DNS (recommended, you said you can do this):**
Add a record so `3573112786.airable.io` resolves to your Pi's IP (e.g. a CNAME
`3573112786.airable.io -> pi.lan`, with `pi.lan -> <pi-ip>`). Nothing else changes —
`ntp.airable.io`, `static.airable.io`, and all other traffic stay untouched.

**Option B — built-in DNS server:**
Set `dns.enabled: true` in the config and point the receiver's DNS fields at the Pi
(Setup → Network → DHCP Off → enter IP/mask/gateway, then DNS (P) and DNS (S) =
Pi's IP). The built-in DNS logs every query and forwards everything not in `override`
upstream, so only the airable host is redirected.

## Verify it works

1. Power-cycle the receiver (standby does not always clear cached DNS).
2. Select `NET RADIO` on the receiver / MusicCast app and browse.
3. If the lists load → the receiver accepted the self-signed certificate, and we're good.
4. Confirm captures appeared:
   ```bash
   ls data/captures/3573112786.airable.io/
   ```
   Each file is one request/response with headers and body in plaintext.

If no captures appear, check `data/handshake.log` for TLS errors — that means the receiver
validates the certificate and the interception path is blocked (see Troubleshooting).

## Reading the captures / feeding back into the offline server

Each capture JSON file is one request/response with headers and body in plaintext.
`app/offline.py` was built directly from the captured flow (authenication → directory
listings → station records → stream redirects). To improve fidelity, browse more of the
receiver's menus while upstream (`mode: proxy`) and, if something in offline mode looks
wrong on the device, share the corresponding capture JSON and it can be mapped in.

## Phone / MusicCast app trust

The streamer ignores certificate validity, but the **MusicCast phone app validates TLS**: the
app also talks to `3573112786.airable.io` directly, and it will refuse a self-signed cert,
showing *"We've encountered a connection error"* in the app even while the receiver itself
works fine (you'll still see the device's requests in `data/captures/`).

Fix: sign the server cert with your own local CA and install the CA on the phone.

1. On the Pi (one-time): `./scripts/gen_ca.sh` — creates `ssl/ca.crt` + `ssl/ca.key`.
   Re-run `./scripts/gen_cert.sh 3573112786.airable.io` afterwards (it will now sign with
   the CA). Then `docker compose up -d --build`.
2. Copy `ssl/ca.crt` to the phone (e.g. send it to yourself and open the file).
3. Install it as a trusted CA certificate:
   - **Android** (11+): Settings → Security → Encryption & credentials → Install a
     certificate → **CA certificate** (older versions: open the `.crt` file in Downloads).
   - **iOS**: open the file and confirm the profile, then Settings → General → About →
     Certificate Trust Settings → enable **full trust** for "Local Radio CA".
4. Restart the MusicCast app.

Browsing and playback from the receiver's own UI/remote works regardless (it ignores the
certificate), so this only affects app-driven control.

## Station manager UI

A small web page to add/remove your stations by hand — no editing YAML or restarting needed.

- Runs in `mode: offline` on an extra HTTP port (default `9080`), e.g. `http://<pi-ip>:9080`.
- Add a station with name, folder, stream URL, codec and bitrate; it's written straight to
  `stations.yaml` and is immediately available on the receiver (saved once, persists).
- Reachable without TLS, on your LAN only — enable/disable via the `ui` block in
  `config.yaml` (`enabled`, `host`, `port`). It is **not** the airable API port; that stays `443`.
- Note: the streamer cannot play HLS/DASH/HTTPS streams — use plain HTTP MP3/AAC URLs.
  Your existing airable favorites that aren't seeded will still 404 unless added here.

## Capturing stations while in proxy mode

While `mode: proxy`, every station you play on the receiver is logged to
`data/stations_seen.yaml` — just the essentials (name, airable id, direct stream URL,
codec, bitrate), one entry per station, deduplicated. It's written in the same shape we
use offline, so it doubles as a growing seed list.

To adopt captured stations into your offline menu:

```bash
python scripts/adopt_stations.py --list            # see what was captured
python scripts/adopt_stations.py                   # merge new ones into stations.yaml
```

New stations land under a `Proxy captured` folder (override with `--folder`); anything
already in `stations.yaml` (same airable id or stream URL) is skipped. Play a favorite
once while upstream is alive, adopt it, and it works offline forever.

Control this with the `stations_log` block in `config.yaml` (`enabled`, `file` — the file
path is relative to `data_dir`, so `stations_seen.yaml` → `data/stations_seen.yaml`).

## Operations

| Item | Location |
| --- | --- |
| Request/response captures (schema) | `data/captures/<host>/` |
| Cached upstream responses (fallback) | `data/cache/<host>/` |
| TLS handshake failures | `data/handshake.log` |
| DNS queries (if DNS server enabled) | `data/dns.log` |

Back up `data/` if you want the fallback cache to persist across reinstalls.

## Troubleshooting

- **Empty list after switching DNS:** power-cycle the receiver off at the mains for 1–2
  minutes; standby can keep stale IP/DNS entries.
- **Republic `502 upstream unavailable`:** upstream was unreachable and no cached response
  exists yet — make sure the Pi can reach the internet and you've browsed the radio at
  least once while airable was up.
- **Port 53 already in use:** `systemd-resolved` on some distros occupies it
  (`sudo systemctl stop systemd-resolved` / mask it) when using the built-in DNS.
- **Captures show up but the phone app says "connection error":** the receiver accepted
  the cert but the MusicCast app rejected it (it validates TLS against airable.io). Install
  the local CA on the phone (see "Phone / MusicCast app trust"). The receiver itself keeps
  working meanwhile.
- **No handshake errors, no captures:** check `docker compose logs`, firewall rules, and
  that the receiver actually resolved `3573112786.airable.io` to the Pi (ping the host on
  your DNS host, or check `data/dns.log` if DNS mode is on).

## Security / limitations

- Traffic to the radio API host is decrypted by your Pi; keep the Pi trusted and on a
  network you control.
- This only intercepts the configured hosts (`3573112786.airable.io`, optionally
  `static.airable.io`). NTP, Yamaha/MusicCast cloud, and broadcasters are untouched.
- Logos will break when airable dies unless you also mirror `static.airable.io`.
- Streaming audio is not proxied — the receiver still connects directly to broadcaster
  stream URLs.