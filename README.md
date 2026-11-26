# Moonie-Cams

Cams translates and copies your cameras' streams. A stream comes in in one format and goes out in
the ones you need: ONVIF, RTSP, MJPEG, HLS, WebRTC or RTMP. A single connection to the real camera,
shared by every client.

A camera that can only serve RTSP or MJPEG becomes an ONVIF camera that any ONVIF client can add, while its stream stays readable over
RTSP, MJPEG, HLS or WebRTC at the same time.

All the streaming is done by [go2rtc](https://github.com/AlexxIT/go2rtc) (MIT) 1.9.15 with its
ffmpeg. Each camera gets its own go2rtc container. `app.py` only turns
`cameras.yaml` into one go2rtc config per camera and a compose file; nothing runs between the
camera and its clients except go2rtc.

- One connection to the source, shared by every client (a Tapo allows one connection per stream,
  an ESP32-CAM one client in all).
- Reconnects on its own when the source drops.
- No recording, no storage.

A step-by-step guide, from the first camera to adding it to an ONVIF client, is in
[`wiki/guide/en/index.md`](wiki/guide/en/index.md) (also [in French](wiki/guide/fr/index.md)).

## Requirements

- A Linux host running Docker with Docker Compose: each copy gets its own address and MAC on the LAN
  through a `macvlan` network, which Docker Desktop on macOS or Windows does not provide.
- Nothing else: every step runs in Docker. [just](https://github.com/casey/just) is optional for a
  deployment (a shortcut for the same steps) and the contributors' tool (`just ci`).
- One free address on the LAN for each copy that serves ONVIF or WebRTC, outside the DHCP range.

## Set up

1. `cp .env.example .env` and set the host interface, LAN subnet and gateway the copies sit on, and
   `TZ`, the time zone of the copies (logs and the ONVIF clock; `Etc/UTC` by default).
2. `cp cameras.example.yaml cameras.yaml` and describe the cameras (format below).
3. Put each password in its own file under `secrets/`, the file named after its key, holding the
   raw password only (a trailing newline is fine; `@`, `^`, `:` or `/` are fine too, see "How it is
   built"; only `"`, `\`, control characters and Unicode spaces are refused):
   - `secrets/<NAME>_SOURCE_PASSWORD` (or whatever `${KEY}` the source URL uses): the password
     go2rtc gives the real camera (the camera's own account);
   - `secrets/<NAME>_OUTPUT_PASSWORD`: the password of the copy's own RTSP account (user `cams`
     unless `output_user` says otherwise), which the clients give the copy.
4. Optional: to be told when a camera goes down or comes back, see "Webhook support".
5. Render and start, one of two ways below. No image is published on any registry: compose builds
   every image from this repository on the Docker host, and the first build of the patched go2rtc
   takes a few minutes (see "How it is built").

Steps 1 to 3 without just, from the repository root (a trailing newline in a secret file is fine; a
password typed on a command line stays in the shell's history):

```sh
cp .env.example .env
cp cameras.example.yaml cameras.yaml
printf '%s' 'the-password' > secrets/C100_SOURCE_PASSWORD
```

On Windows, in PowerShell: `Copy-Item`, and `Set-Content -NoNewline -Path secrets\C100_SOURCE_PASSWORD -Value 'the-password'`.

### Deploy with Docker Compose, or as a Portainer stack (no just)

```sh
docker compose -f tools/compose.yml run --rm render
docker compose --env-file .env -f generated/compose.yml up -d --build --remove-orphans
```

The first command renders `cameras.yaml` into `generated/` in the tool image (Docker only, no Python
on the host; `tools/compose.yml` sits in its own folder so the stack's `.env` never renames it). The
second builds the images and starts every copy; `logs -f [service]` and `down` take the same
`--env-file .env -f generated/compose.yml`. On a Linux host the rendered files belong to root, as
with `just render`; add `--user "$(id -u):$(id -g)"` after `run` to keep them yours.

Portainer cannot run the render, and a Git-repository stack cannot work here: Portainer documents
image builds from a Git stack as not fully supported (and failing on agent environments since
2.29.2), and the Community Edition mounts no file from the repository (relative paths are a Business
Edition feature), while every copy mounts its rendered config. What works is its web editor, with the
files on the Docker host: render there with `--host-dir`, the absolute path of `generated/` on that
host, so the compose file names its config files and secrets (`<host-dir>/../secrets/<KEY>`) by
that path:

```sh
docker compose -f tools/compose.yml run --rm render cameras.yaml generated --env=.env --host-dir=/opt/moonie-cams/generated
docker compose --env-file .env -f generated/compose.yml build
```

Then add a stack named as `COMPOSE_PROJECT_NAME` (default `moonie-cams`) with the web editor, paste
`generated/compose.yml`, and load `.env` as its environment variables. Portainer may warn that a
secret file does not exist (its compose looks inside its own container); the copy still gets it
from the host. To update, render and build again, paste the new file and update the stack.
`just webeditor-check` proves it: it renders into a volume on the host, builds there, and deploys the
compose text alone from an empty folder.

### Deploy with just

`just up` renders (the same `tools/compose.yml`), builds and starts. Then `just logs`,
`just logs cam-c100`, `just down`.

### Several stacks, and the checks

Several stacks on one Docker host (for example one per camera VLAN): give each checkout its own
`COMPOSE_PROJECT_NAME` in `.env` (default `moonie-cams`). The project's volume, `lan` network and
webhook container (`<project>-cams-webhook-1`) are then its own; camera names must still differ
between stacks, since each copy's container is `cams-<name>`.

`just check` lints and runs every test, renders the example and validates it with `docker compose config`.
`just ci` runs `just check` and is the merge gate, and the only CI: there is no hosted pipeline, and a
change merges only on a green local `just ci`.

Before publishing a fork or a change, `just privacy-check` scans every tracked file for the words that
would identify your own deployment (host names, domains, room or camera names, address ranges). Keep that
list outside the repository, one word per line (`#` starts a comment, matching ignores case), and point
`PRIVATE_WORDS_FILE` at it: `PRIVATE_WORDS_FILE=~/cams-private-words just privacy-check`. A hit names the
file, the line and the word's line in your list, never the word. `just test` runs the same check skipped,
plus a generic one that refuses any private (RFC 1918) address: examples use 192.0.2.x, 198.51.100.x and
203.0.113.x.

## Add a camera

Add an entry to `cameras.yaml`, add its secret files, run `just up`.

```yaml
cameras:
  - name: c100                       # also the stream path: rtsp://<ip>/c100
    input: rtsp                      # rtsp | onvif | rtmp | hls | mjpeg
    source: rtsp://cams:${C100_SOURCE_PASSWORD}@192.0.2.12:554/stream1
    source_sub: rtsp://cams:${C100_SOURCE_PASSWORD}@192.0.2.12:554/stream2   # optional
    outputs: [onvif, mjpeg]          # onvif | rtsp | rtmp | hls | webrtc | mjpeg
    ip: 198.51.100.50                # the copy's own address; needed for onvif and webrtc
```

Examples per source type:

| Source           | `input` | `source`                                              |
|------------------|---------|-------------------------------------------------------|
| Tapo (C100/C210) | `rtsp`  | `rtsp://<account>:${C100_SOURCE_PASSWORD}@<ip>:554/stream1` (`stream2` for the small one) |
| ESP32-CAM        | `mjpeg` | `http://<ip>:81/stream`                               |
| OctoPrint        | `mjpeg` | `http://<ip>/webcam/?action=stream`                   |

Optional keys:

| Key           | Default                          | What it does                                             |
|---------------|----------------------------------|----------------------------------------------------------|
| `mac`         | a stable one derived from the name | the copy's MAC on the LAN                              |
| `output_user` | `cams`                           | user of the copy's RTSP account                          |
| `rtmp_target` | none                             | the URL the `rtmp` output pushes to, `${KEY}` allowed    |
| `ports`       | none                             | `{http, rtsp}` host ports, for a camera without `ip`     |
| `fps`         | `10` when transcoded             | 1 to 60: the H.264 encoder's frame rate (keyframe every 2 s) and the advertised one |
| `bitrate`     | `600` when transcoded            | kbit/s, 1 to 100000: the encoder's cap (`-maxrate`) and the advertised one |
| `size`        | none                             | `WIDTHxHEIGHT`, e.g. `1280x720`: the advertised size, only used while the stream is not running |
| `mode`, `onvif_url`, `ptz_hold`, `proxy_log` | none | relay the camera's own ONVIF, see "Proxy mode"           |
| `location`    | none                             | announced over ONVIF (the `location` scope); printable text, 1 to 64 characters; needs the `onvif` output, refused in proxy mode |
| `mirror_state` | `true`                          | `false` keeps the copy up while its camera is down, see "State mirror" |
| `backchannel` | `false`                          | `true` lets a WebRTC client play audio through an `rtsp`/`onvif` camera's speaker (go2rtc's backchannel); off, every source gets `#backchannel=0` |

`fps` and `bitrate` drive the encoder only when go2rtc transcodes to H.264 (an MJPEG source). For
a passed-through camera nothing is re-encoded: they only set what ONVIF advertises, and are left
out unless given. The live stream's size and frame rate always win over `size` and `fps`, so
`size` is rarely needed. A proxied camera (`mode: proxy`) advertises the real camera's own profiles,
so it refuses `fps`, `bitrate` and `size`.

### Formats and transcoding

An output may never be the input's own format: `app.py render` refuses it and names the camera
(unless the camera sets `mode: copy`, below).
The ONVIF output serves its RTSP stream too, so a camera with `outputs: [onvif]` already serves
RTSP too. go2rtc runs ffmpeg only when an output needs a video codec the input lacks, and
only while such a client is connected:

| Input \ output                  | onvif, rtsp, rtmp, hls, webrtc (H.264) | mjpeg      |
|---------------------------------|----------------------------------------|------------|
| rtsp, onvif, rtmp, hls (H.264)  | passed through                         | ffmpeg     |
| mjpeg (ESP32-CAM, OctoPrint)    | ffmpeg to H.264                        | refused    |

Every config overrides go2rtc's H.264 encoder (Main profile, one slice per frame, limited-range
yuv420p): with the default one, UniFi Protect decodes only the first slice and shows green below it.
It also fixes the frame rate (`fps`, default 10) and caps the bitrate (`bitrate`, default 600 kbit/s),
so a transcoded camera stays small.

H.264 is assumed for every non-MJPEG source; an H.265 source is not handled for now.

## Copy mode

`mode: copy` turns a camera into a plain splitter: one connection to the source, copied to every
output at once, the input's own format included (passed through untouched). Use it for a source
that allows one client, so that several clients share that one connection:

```yaml
cameras:
  # A camera that allows one RTSP client: its stream shared by several clients, plus MJPEG.
  - name: c200
    mode: copy
    input: rtsp
    source: rtsp://cams:${C200_SOURCE_PASSWORD}@192.0.2.14:554/stream1
    outputs: [rtsp, mjpeg]
    ip: 198.51.100.54
```

Every RTSP client reads `rtsp://cams:<password>@198.51.100.54:554/c200` (one RTSP
output, any number of clients), and a browser reads
`http://198.51.100.54/api/stream.mjpeg?src=c200_mjpeg`.

What each output gets:

- **Its own buffer.** go2rtc gives every client its own queue and writer: a late client drops its
  own packets and never holds the others.
- **Its own ffmpeg.** Every output that re-encodes reads its own stream, `<name>_<output>`, with
  its own ffmpeg (`c200_mjpeg` above). A crashed or slow encoder hits that output only. ONVIF and
  RTSP are one RTSP server, so they share the main stream. This costs one encoder per output,
  where the default mode shares one per codec. Every H.264 encoder uses the camera's `fps` and
  `bitrate` (one encoder template per copy), so each output is as small as the default mode's.
- **Its own restart.** A client that leaves or dies is removed alone; the source connection stays
  up while any client is left. go2rtc reconnects a dropped source by itself and moves every client
  over, and retries a failed RTMP push every 5 s.
- **Its own health.** `just health c200` prints, per stream, the source state, the clients, the
  kbit/s and the drops of each output. It reads go2rtc's `/api/health` (added by
  `go2rtc/health-endpoint.patch`): read-only, and it never shows a URL, an address or a secret.
  The container's healthcheck uses it too.

With an `onvif` output, the extra `<name>_<output>` streams also show up as ONVIF profiles: go2rtc's
GetProfiles lists every stream, and neither upstream nor `go2rtc/onvif-profile.patch` has a setting
to hide one. Hiding them would take a change to that patch; until then, pick the main stream (and
`<name>_sub`) in the ONVIF client.

Copy and proxy do not combine: `mode` takes one value, and `app.py render` refuses both at once.
The proxy hands out the real camera's own profiles and stream paths, which know nothing of copy
mode's per-output streams.

`just copy-check` renders `tests/copy_check/cameras.check.yaml` (one copy-mode camera on a local
test source, with RTSP, HLS and MJPEG outputs), plays two RTSP clients, MJPEG and HLS against it,
freezes and kills the MJPEG client and checks the others keep going and the source keeps one
connection.

### What go2rtc already isolates (1.9.15)

- One connection per source: each stream source is one producer, and every client of that codec
  reuses its track (`internal/streams/producer.go:93`).
- One queue per client: each client track is a `Sender` with its own bounded channel and goroutine;
  a full queue drops that client's packets (`pkg/core/track.go:78-115`).
- An `ffmpeg:` source is one producer of its stream, shared by every client of its codec
  (`internal/streams/producer.go:93`), hence a stream per re-encoding output in copy mode.
- A dropped source is redialled after 1 s, then 5 s, 10 s, 1 min, and the clients are moved to the
  new connection (`internal/streams/producer.go:160-243`). The source stops once the last client
  leaves (`internal/streams/stream.go:97-119`).
- An RTMP push that fails or ends, the target refusing it included, is retried every 5 s
  (`internal/streams/publish.go:15-22`, `internal/rtmp/rtmp.go:133-143`). It is never retried if
  the source cannot be read when go2rtc starts (`publish.go:11-13`): restart the container then.
- `/api/streams` shows each source and accepts PUT, PATCH and POST (`internal/streams/api.go:13-90`),
  and `allow_paths` is one list for every listener (`internal/api/api.go:150-160`), so it is never
  allowed; `/api/health` replaces it for health.

## State mirror: a copy is down while its camera is

Every copy, in every mode, has a state sentinel next to it (`cam-<name>-mirror`, built from
`sentinel/`). While the real camera is down, the copy looks unplugged: no ping, no ONVIF, no RTSP,
nothing. It comes back on its own with the camera. Without it, a copy whose camera is off keeps
answering ONVIF, and an ONVIF client shows it online with no picture.

- **Probe.** Every 5 s the sentinel opens a TCP connection to the source's host and port (from
  `source`; rtsp 554, rtsps 322, onvif and http 80, https 443, rtmp 1935 when the URL gives none)
  and closes it at once, with a 2 s timeout. No session is opened: a Tapo allows one connection
  per stream. Not ping: a camera whose streaming service has hung still answers ICMP, and ping
  needs a capability the sentinel does not get. The sentinel is only given that host and port,
  never the account.
- **Debounce.** 3 failed probes in a row close the copy, 2 successes in a row open it again. At
  start, the first probe decides at once. Each change is one log line (`just logs cam-c100-mirror`).
- **Closed** means one nft table (`inet moonie_mirror`) in the copy's network namespace that drops
  every inbound packet but loopback, packets from the camera's own address (so the probe, go2rtc
  and the ONVIF proxy still hear the camera), replies to flows the copy itself opened (`ct
  direction reply`: DNS answers, the probe, the proxy to the camera, an RTMP publish) and IPv6
  neighbour discovery. Drop, not reject: nothing comes back, as from an unplugged camera. A
  client's connection is never a reply to the copy, so one opened before the copy closed goes
  silent too. **Open** means no table at all.
- **NET_ADMIN, in the copy's namespace only.** The sentinel runs with `network_mode:
  service:cam-<name>`, every capability dropped but `NET_ADMIN`, a read-only root and
  `no-new-privileges`. That capability reaches the one network namespace it shares with its copy:
  never the host's, nor another copy's.
- **go2rtc restarts.** A restarted go2rtc gets a new namespace, with no table. The sentinel notices
  that go2rtc is gone from 127.0.0.1 (its RTSP port, or its API port when it has no RTSP server),
  exits, and Docker restarts it into the new namespace, where it probes and applies afresh.
- **go2rtc stays down.** If go2rtc is down longer than that (about 15 s, for example `docker
  compose stop cam-x`, later `docker compose start cam-x`), Docker cannot join the stopped
  namespace and the sentinel stays exited: no restart policy or `depends_on` setting brings it back
  on a plain `start`. Run `docker compose up -d` (`just up`), which starts it again.
- **Stops.** A stopped sentinel (SIGTERM) removes its table first, so it never leaves the copy
  closed. One killed hard (SIGKILL) leaves the table in the copy's namespace: the copy stays closed
  until the sentinel starts again (it probes and applies at once) or the copy restarts (new
  namespace, no table). `just up` does either.
- **Opt out** with `mirror_state: false` on a camera. A source with no host to probe (`ffmpeg:`,
  `exec:`) needs it; `app.py render` says so. A copy that listens on nothing (an RTMP push only)
  has no sentinel. A host name is resolved again at each probe, also while the copy is closed (the
  DNS answer is a reply to the copy's own query). A scoped IPv6 literal (`fe80::1%25eth0`) is
  refused: give the camera's global or unique local address, or set `mirror_state: false`.

`just mirror-check` proves it on a test network, with the source given by a name a DNS server on
that network answers: a client sees a stand-in copy answer, the fake camera is killed and the copy
goes dark (no TCP answer, no ping reply, and a client's connection opened before gets no answer
either), a stopped sentinel opens it, a started one closes it again, the camera comes back and so
does the copy, after the copy's container restarts the sentinel rejoins its new namespace and
closes it again, and after the copy stays stopped past the sentinel's restart, `docker compose up
-d` brings the sentinel back.

## Webhook support

When a source camera goes down or comes back, Cams POSTs one JSON message to an HTTP endpoint of
your choice: anything that accepts a JSON POST. It is built on the state mirror: a camera with
`mirror_state: false` sends nothing.

The body, `Content-Type: application/json`:

```json
{"camera": "c100", "state": "offline", "since": "2026-11-23T15:52:30Z", "source": "192.0.2.12:554", "host": "moonie-cams"}
```

| Field    | What it is                                                                       |
|----------|----------------------------------------------------------------------------------|
| `camera` | the camera's `name` in `cameras.yaml`                                            |
| `state`  | `offline` (the copy just closed) or `online` (it just opened)                    |
| `since`  | when the sentinel saw that state, UTC ISO 8601                                   |
| `source` | the host and port the sentinel probes (`[v6]:port` for IPv6), never the account  |
| `host`   | the compose project (`COMPOSE_PROJECT_NAME`, default `moonie-cams`), to tell stacks apart |

With a token, the request also carries `Authorization: Bearer <token>`.

### Set it up

1. Create the endpoint: any HTTP receiver that accepts a `POST` with a JSON body. Note its URL.
2. In `.env`:
   - `CAMS_WEBHOOK_URL`: that URL, `http://` or `https://`;
   - `CAMS_WEBHOOK_NETWORK`: the existing Docker network the endpoint is reachable on (for
     example the network the receiver's container is on). Required whenever `CAMS_WEBHOOK_URL` is set: `just render` fails early
     without it;
   - `CAMS_WEBHOOK_ON_START`: `true` to also post the state of each camera `cams-webhook` sees
     for the first time, at its start or later (default `false`: only changes are posted).
3. Optional token: put it in `secrets/CAMS_WEBHOOK_TOKEN` (one line, printable ASCII, no space),
   readable by uid 65534 (`chmod 644` on Linux), and check it on the endpoint's side (header
   `Authorization`, value `Bearer <token>`). The file is
   mounted only when it exists at `just render`.
4. `just up`. `just logs cams-webhook` shows one line per message sent.

### How it works

- **Each sentinel writes, never posts.** A sentinel sits in its copy's network namespace, on the
  camera's VLAN, which may not reach the endpoint (an isolated VLAN). So it only writes its state
  to `<camera>.json` on a small named volume of the project (`state`, mounted at `/state`, the one
  writable place of its read-only root), at start and at each change: written whole under a hidden
  name, then renamed over the old file, so a reader never sees half of it. It holds no secret.
- **One `cams-webhook` service per project** (container `<project>-cams-webhook-1`, e.g.
  `moonie-cams-cams-webhook-1`, so two projects on one host never collide; built from `webhook/`,
  one Python stdlib file) mounts that volume read-only, reads it every 2 s, and posts each state
  that differs from the last one it saw for that camera. It tracks 256 cameras at most (one log
  line when that cap is hit). It joins `CAMS_WEBHOOK_NETWORK` only, never a camera VLAN. With
  `CAMS_WEBHOOK_URL` empty it joins no network at all (`network_mode: none`), logs one line that the
  webhook is off and idles. It is rendered whenever at least one camera has the mirror on.
- **One start rule.** It keeps the last state per camera in memory. The state of a camera it sees
  for the first time (at its own start, or a camera added later) is only remembered, never posted,
  unless `CAMS_WEBHOOK_ON_START=true`; only the changes after that are posted. So a restart never
  repeats old news, and a sentinel that restarts with the same state posts nothing.
- **No replay.** Changes that happen while `cams-webhook` is down are not replayed: after its
  restart it only knows the states it finds then. Changes while the endpoint is down are not
  replayed either: each camera has a one-slot mailbox holding only its latest pending state, so a
  camera that flaps during an outage gives at most its latest state once the endpoint is back.
- **Failures.** A post is tried 4 times (after 2, 5 then 10 s), then logged and dropped. A `4xx`
  answer other than `408` and `429` is final: logged and dropped at once, never retried. Each
  camera has its own mailbox and worker: a slow or failing endpoint never delays another camera.
  Redirects are not followed (they would carry the token elsewhere). The token and the URL's path
  (which often carries the endpoint's secret) are never logged.
- **The state volume is shared.** Every sentinel mounts it read-write, so any sentinel can write
  any camera's file. `cams-webhook` treats each file as hostile: it never follows a symlink, never
  blocks on a FIFO, skips anything that is not a regular file of at most 4 KiB, and skips a file
  whose `camera` is not its own file name (`c100.json` speaks for `c100` only). The body is built
  from `camera`, `state` (`online` or `offline`), `since` and `source` only, each a string of at
  most 64 characters, plus `host`; a file that fails this is skipped. A skipped file is logged
  once by name, never by content.
- **Hardened.** Every capability dropped, read-only root, `no-new-privileges`, uid 65534. The URL
  comes from `.env` through docker compose and never lands in `generated/`.
- The camera name `webhook` is reserved (its container `cams-webhook` would read as the webhook service).

`just webhook-check` proves it: a fake camera, a stand-in copy and its sentinel, `cams-webhook` and a
fake endpoint, with no network between the sentinel and the endpoint. Nothing is posted at start;
the camera is killed and the endpoint gets `offline`; it comes back and the endpoint gets `online`,
each once, with the token header.

## Outputs of one copy

| Output  | Where                                        | Auth                   |
|---------|----------------------------------------------|------------------------|
| ONVIF   | `http://<ip>/onvif/device_service` (port 80) | none (see limits)      |
| RTSP    | `rtsp://<ip>:554/<name>` (and `<name>_sub`)  | the copy's RTSP account |
| MJPEG   | `http://<ip>/api/stream.mjpeg?src=<name>`    | none                   |
| HLS     | `http://<ip>/api/stream.m3u8?src=<name>`     | none                   |
| WebRTC  | `http://<ip>/api/webrtc?src=<name>`, port 8555 | none                 |
| RTMP    | pushed to `rtmp_target`                      | the target's           |

Only the go2rtc API paths the outputs need are enabled: the web UI, config editing and the stream
list (which would show source URLs) are switched off.

## Proxy mode: a full copy of a Tapo (PTZ, events)

A Tapo (C100, C210 with pan-tilt) accepts one connection per stream, and several clients may want
it. With `mode: proxy`, the copy presents itself as the camera: a small ONVIF
proxy (`proxy/onvif_proxy.py`, Flask + waitress + requests + lxml, pinned) relays ONVIF calls
(device, media, PTZ, events, imaging; only reads, PTZ commands and event calls, since any other
write would change the camera for every client, see "What is refused") to the real Tapo, and
go2rtc holds the single RTSP session per stream (`stream1` as `<name>`, `stream2` as
`<name>_sub`), passed through, never transcoded.

```yaml
  - name: c210_a
    input: rtsp
    source: rtsp://cams:${C210_A_SOURCE_PASSWORD}@192.0.2.10:554/stream1
    source_sub: rtsp://cams:${C210_A_SOURCE_PASSWORD}@192.0.2.10:554/stream2
    outputs: [onvif]                  # needed; mjpeg, hls, webrtc may be added
    ip: 198.51.100.51
    mode: proxy
    onvif_url: http://192.0.2.10:2020   # the Tapo's ONVIF port is 2020
    ptz_hold: 5                       # optional, seconds
    proxy_log: writes                 # optional: all, writes (the default) or off
```

`onvif_url` is the camera's own address and nothing else: `http://<IPv4>[:port]`, port 1 to 65535
(80 when left out), with no account, path, query or fragment; `app.py render` refuses anything
else, and the proxy checks it again when it starts. `ptz_hold` is a number of seconds above 0, at
most 600. `proxy_log` is one of `all`, `writes` or `off` (see "Proxy log"); left out, compose
sets nothing and the proxy's default, `writes`, applies.

- **One account.** Clients give the copy the Tapo's camera account, for ONVIF and RTSP alike: the
  proxy passes the WS-Security header (in the SOAP Header) and any HTTP digest headers through,
  and go2rtc's RTSP server takes the account of the `source` URL. `output_user` and
  `<NAME>_OUTPUT_PASSWORD` are not used in this mode.
- **What reaches the Tapo.** Not the client's bytes, but the request tree the proxy checked,
  serialized again in UTF-8 with no XML declaration, as clients send it (with its Content-Length and
  `charset=utf-8`): comments are dropped and
  CDATA becomes escaped text, so the Tapo's parser cannot see an operation the proxy did not (a
  `SystemReboot` hidden in a comment or a CDATA section never reaches it). The WS-Security
  UsernameToken still validates: its digest covers the nonce, the time and the password, no bytes
  of the body.
- **What is rewritten.** Only the answers, and only where they name the camera: stream URIs point
  to go2rtc (`rtsp://<ip>:554/<name>`), snapshot URIs to go2rtc's
  `http://<ip>:1984/api/frame.jpeg?src=<name>`, and every other occurrence of the Tapo's address
  (XAddrs, service and PullPoint addresses, any text or attribute) becomes the copy's address, its
  port 2020 the port the client used. An `https://<tapo>` URL becomes `http://<ip>` on the port the
  client used, whatever its port was: the copy serves no HTTPS, and the proxy relays that path to
  the Tapo's ONVIF port. GetNetworkInterfaces gives the copy's IP and MAC,
  GetHostname `cams-<name>`, GetNetworkProtocols the ports the copy serves (HTTP 80 and 2020, RTSP
  554, no HTTPS). GetNetworkInterfaces drops IPv6: the copy has none, and the Tapo's link-local
  address is made from its MAC. A stream URI that carries an account (`rtsp://user:pw@<tapo>/...`)
  points to go2rtc without it. GetDeviceInformation is untouched, so a client shows the Tapo's model
  and firmware. An answer that is not SOAP (an HTML error page) gets the same address rewrite as
  text. An answer with nothing to rewrite is relayed byte for byte.
- **What is not rewritten.** Anything that does not name the Tapo's own address is relayed as the
  Tapo answers it:
  - the gateway, DNS and NTP addresses (GetNetworkDefaultGateway, GetDNS, GetNTP, and those in
    GetNetworkInterfaces): they are the LAN's, and the copy shares the LAN;
  - the serial number and hardware ID in GetDeviceInformation, which a Tapo may derive from its
    MAC, and the scopes (GetScopes: name, location, hardware, sometimes a MAC): a client may
    show them, and they identify the real camera, not its copy;
  - any address given by host name other than the one in `onvif_url`, and an `http://<tapo>:<port>`
    URL on another port than the ONVIF one keeps that port.
- **What is refused (fail closed).** A request that is refused gets a SOAP fault (`env:Sender`) and
  never reaches the camera:
  - any non-empty body that is not one clean SOAP 1.2 or 1.1 envelope: not well-formed XML, a
    DOCTYPE, entity or processing instruction, anything before or after the envelope (a comment
    included), more than one Body, an empty Body, text in it, a Header or Body outside the
    envelope's own namespace, or an operation outside the ONVIF services (no namespace, an unknown
    one);
  - an empty body whose SOAPAction (or Content-Type `action`) names any operation;
  - an ambiguous action header, since a camera may dispatch on the header rather than the Body:
    an action URI that, once its outer quotes are stripped, holds whitespace, a control or
    non-ASCII character, `,`, `;`, `?`, `#`, `"` or `%`; two SOAPAction or Content-Type headers;
    a Content-Type with more than one `action` parameter, an extended one (`action*=`), `action`
    anywhere else in it (in the media type, another parameter's name or value: `action=...;
    charset=utf-8`, `xaction=...`, `soap+xml action=...`), a quoted `;` or an unterminated quote; a
    WS-Addressing Action that holds an element, not only its URI as text; and a SOAPAction and a
    Content-Type `action` that name different operations;
  - every call that is not a `Get…`, by its Body, its SOAPAction, its Content-Type `action` or its
    WS-Addressing Action, unless it is a PTZ command (`ver20/ptz/wsdl`, see below) or an event call
    (CreatePullPointSubscription, PullMessages, SetSynchronizationPoint in `ver10/events/wsdl`;
    Renew, Unsubscribe in WS-BaseNotification `b-2`, with their action URIs under `bw-2`). The rule
    is an allowlist, whatever the namespace: Device (network, users,
    certificates, clock, scopes, relays, firmware, restore, reboot, factory reset), Media and
    Media2 (encoder and source configurations, profiles, OSD, multicast), Imaging (image settings,
    focus), DeviceIO, Recording, Analytics, and any call or service the proxy does not know. There
    is one real camera behind every copy and client, so one client's SetVideoEncoderConfiguration
    would change the main stream for all of them. The ONVIF clients tested only read these
    services to add and stream a camera (profiles, stream and snapshot URIs, options); a client's
    reboot, set-time, IR lamp and autofocus controls belong on the real camera, not on its copy.
    The Device writes are also refused when they are nested anywhere in the Body;
  - WS-BaseNotification `Subscribe` (push mode), by its Body, its action headers or nested
    anywhere in the Body: it would make the real Tapo POST its notifications, with its own
    identity, to any address the client gives, and so open connections to any host. A client
    falls back to PullPoint subscriptions, which are relayed;
  - namespaces are compared without case and trailing slashes
    (`HTTP://www.onvif.org/ver10/device/wsdl/` is the Device service);
  - a body over 1 MiB (HTTP 413), and a malformed Host header.
- **Ports.** The proxy answers ONVIF on port 80 (where clients look by default) and 2020 (like a Tapo). go2rtc's
  API moves to port 1984 in this mode: MJPEG, HLS and WebRTC of a proxied copy are there.
- **Tapo down.** The client gets a clean SOAP fault, `camera unreachable`.
- **No session.** Each call is one plain request to the Tapo: no cookie, proxy setting or `.netrc`
  is kept or read.
- **No privilege.** The proxy container drops every capability but binding port 80, runs on a
  read-only root with `no-new-privileges` and a 16 MB `/tmp` (waitress may spill a large body
  there), and installs only hash-checked packages. `just proxy-check` runs it that way.

### PTZ arbitration

Every client drives PTZ through the proxy. Priority goes to the first who sent a
command: the first client (by source IP) that sends a PTZ command (a PTZ call of ptz.wsdl that is
not a `Get…`: moves, Stop, presets; any other is refused) holds PTZ until `ptz_hold` seconds
(default 5) after its last PTZ command. The other client's commands get a SOAP fault
(`env:Receiver`, "PTZ is in use by another client") rather than a silent success: a fake success
would let its UI believe the camera moved,
while a fault shows that the command was dropped, and the ONVIF clients tested handle it as a failed
call. Stop counts as a command, so the loser cannot stop the holder's move. Reads (GetStatus,
GetPresets, …) are never blocked. Only a command the camera answers with a 2xx that is not a SOAP
Fault takes the hold, so a refused one (bad credentials) takes none. A PTZ command name nested
anywhere in the Body also sends the request through the arbitration. The busy check, the relay
and the claim are one step: a second command sent while the first is still with the camera waits
for its answer (up to 10 s), then finds PTZ taken.

### Proxy log

The proxy writes one line per request on its stdout (`just logs cam-<name>-onvif`), flushed at
once, so PTZ and refusals can be followed as they happen:

```text
2026-11-23T10:00:00Z 198.51.100.6 /onvif/service GetProfiles relayed 200 8ms
2026-11-23T10:00:01Z 198.51.100.5 /onvif/service ContinuousMove[pan=0.5 tilt=0 zoom=0 profile=profile_1] ptz-claim 200 41ms
2026-11-23T10:00:03Z 198.51.100.6 /onvif/service GotoPreset[preset=3 profile=profile_1] ptz-busy:holder=198.51.100.5,left=3.0s - 0ms
2026-11-23T10:00:03Z 198.51.100.5 /onvif/service Stop[pantilt=true zoom=true profile=profile_1] ptz-hold 200 30ms
2026-11-23T10:00:04Z 198.51.100.6 /onvif/device_service SystemReboot refused:camera-command - 1ms
```

The fields, separated by one space:

1. the time, UTC, to the second;
2. the client's IP;
3. the path of the service called (never its query);
4. the operations of the checked Body (their local names, comma-separated), or `-` for an empty
   body or one refused before its operations are known; a PTZ command's are followed by its
   summary in brackets (see below);
5. the decision:
   - `relayed`: sent to the camera;
   - `refused:<reason>`: never reached the camera; the reason is `write` (not a read, PTZ command
     or event call), `camera-command` (a Device command or Subscribe, nested ones included),
     `action` (an ambiguous action header), `action-without-body`, `not-xml`, `doctype`, `entity`,
     `not-soap`, `empty-body`, `not-onvif`, `host` (a malformed Host header) or `too-large`;
   - `ptz-claim`: a PTZ command that takes the hold, `ptz-hold`: one from the holder, that keeps it
     (a PTZ command the camera does not accept is only `relayed`, and takes nothing);
   - `ptz-busy:holder=<ip>,left=<seconds>s`: refused because another client holds PTZ (`-` for
     both when the hold is still being decided by a command in flight);
   - `error:camera-unreachable`: the camera did not answer;
6. the camera's HTTP status, or `-` when nothing was relayed;
7. the duration, in milliseconds.

The path and the operation names come from the client: they are percent-encoded (no space, control
or non-ASCII character) and cut at 160 characters, so a request cannot forge a second line. The log
never holds a body, a header (`Authorization` included), a WS-Security field, a user name, a
password, a query or anything the camera answers.

**PTZ summary.** A PTZ command (of the PTZ service, the first one of the Body) is followed, with no
space, by `[...]`: what it asks for, read from the checked Body only, never from the SOAP Header
(which carries WS-Security), at every level that writes its line, refused and busy lines included.
Only these fields are written, found by their local names below the operation:

| Command | Fields |
|---|---|
| `ContinuousMove` | `pan` `tilt` `zoom` (Velocity: PanTilt x/y, Zoom x), `timeout` |
| `RelativeMove` | `pan` `tilt` `zoom` (Translation), `speed.pan` `speed.tilt` `speed.zoom` (Speed) |
| `AbsoluteMove` | `pan` `tilt` `zoom` (Position), `speed.pan` `speed.tilt` `speed.zoom` (Speed) |
| `Stop` | `pantilt` `zoom` (`true` or `false`) |
| `GotoPreset`, `RemovePreset` | `preset` (PresetToken) |
| `SetPreset` | `preset` (PresetToken), `name` (PresetName) |
| `GotoHomePosition` | `speed.pan` `speed.tilt` `speed.zoom` |
| `SetHomePosition` and every other PTZ command | none of their own |

Every PTZ command then gets `profile` (its ProfileToken). A field that is absent is left out, and a
command with no field at all gets no brackets. Numbers are written as parsed floats rounded to 3
decimals (`0.5`, `-1`, `0`); anything that is not a finite number (text, `inf`, `nan`, hex) is `?`.
A field given twice, or holding an element, is `?` too. Tokens, names and the timeout are
percent-encoded and cut at 40 characters (`...` marks a cut), and the whole summary at 200. The
summary may hold spaces between its fields, never `]`, a control or a non-ASCII character: the line
still reads field by field once the bracketed summary is taken as part of field 4.

`PROXY_LOG` (set by `proxy_log` in `cameras.yaml`) picks how much is written; the proxy refuses to
start on any other value:

- `all`: every request;
- `writes` (the default): every call that is not a `Get…`, except the event calls a client repeats
  forever (`PullMessages`, `Renew`); every refusal and PTZ decision; every call of the PTZ service,
  its reads included; and every error (camera unreachable, or a camera status of 400 or more but
  401, the challenge an HTTP digest exchange starts with). The PullMessages long polls
  and the clients' `GetSystemDateAndTime` and other reads stay out;
- `off`: nothing.

### Add a proxied copy to a client

1. **Remove the real camera first** from every client that reads it directly: the copy holds the
   only RTSP session of each stream, so a client cannot read both.
2. Add the copy as an ONVIF camera by its IP (port 80 or 2020) with the real camera's account. There
   is no WS-Discovery for now: the copy does not show up on its own, it is added by IP only.
3. The client sees the copy as its own camera (its own IP and MAC), with the real camera's model,
   and gets the streams, PTZ and the events through it.

A tool that keeps talking to the real camera for its own controls (privacy mode, alarm, LED…) may
stay, as long as it never opens RTSP on it: it would take the camera's only session.

## Add a copy to an ONVIF client

1. Add the copy as an ONVIF camera by its IP (ONVIF on port 80). Without WS-Discovery, it may not
   show up in the client's search on its own.
2. Give the copy's RTSP account: user `cams` (or `output_user`), password from
   `secrets/<NAME>_OUTPUT_PASSWORD`.
3. The copy announces itself after its container, `cams-<name>` (manufacturer `Moonie-Cams`, model
   `Cams`), the name `docker ps` and `just logs` show.
4. Each go2rtc stream is one ONVIF profile, so `source_sub` gives the client a low-quality stream.
   Some clients keep the profile values they saw when the camera was added: if the streams change,
   remove the camera from the client and add it again.

UniFi Protect adds a third-party ONVIF camera only once "Discover Third-Party Cameras" is on
(Settings > System), through "Advanced Adoption" when the copy is not listed.

## Read a copy over RTSP

Any RTSP client reads `rtsp://cams:<password>@<ip>:554/<name>` (`<name>_sub` for the small
stream). A plain copy carries no pan-tilt and no events: use `mode: proxy` for those.

## How it is built

### go2rtc is built from source, with four patches

Upstream go2rtc's ONVIF server advertises every stream as 1920x1080 at 30 fps and 8192 kbit/s,
whatever the stream really is. A client shows those values, and may size its storage from them.
`go2rtc/onvif-profile.patch` makes it advertise, for each value:

1. the stream's own size and frame rate, read from the H.264 SPS of the stream's producer when one
   is already running (answering ONVIF never starts the stream);
2. otherwise the container's `ONVIF_WIDTH`, `ONVIF_HEIGHT`, `ONVIF_FPS` and `ONVIF_BITRATE`
   (kbit/s), which `app.py` sets from the camera's `size`, `fps` and `bitrate`;
3. otherwise upstream's 1920x1080, 30 fps, 8192 kbit/s.

The bitrate has no live source: it always comes from step 2 or 3.

`go2rtc/health-endpoint.patch` adds a read-only `/api/health`, which copy mode uses (see "Copy mode").
It never waits on a source that is being dialled (that one is reported `busy`), so the healthcheck
cannot time out on a slow camera.

`go2rtc/no-api-source.patch` makes every API lookup (`/api/frame.jpeg`, `/api/stream.mjpeg`, HLS,
MP4 and every `/api/ws` client) find only a stream defined in the config. Upstream let any client
that reaches the API create a stream from a `src` URL, which makes go2rtc dial any host, or
re-point a camera's stream with `?src=<url>&name=<camera>`. Pushing a client's own producer into a
stream is refused with 403 too: over HTTP or WebSocket with `dst=`, and a WHEP offer on `src=`
that sends video, which upstream turned into a producer. A WebSocket offer's own `ice_servers` are
ignored, so a client cannot make go2rtc send STUN or TURN to a host of its choosing. RTSP
publishing is unchanged: the copy's own ffmpeg uses it on loopback, and a public RTSP listener
asks for the copy's RTSP account.

`go2rtc/onvif-vendor-identity.patch` gives each copy its own identity. Upstream announces every
server as go2rtc (GetDeviceInformation's Model, and the `name/go2rtc` and `location/github` scopes),
so a client named every copy "go2rtc". The patch reads `ONVIF_NAME`, `ONVIF_MANUFACTURER`,
`ONVIF_MODEL` and `ONVIF_LOCATION` instead (scope values URL-escaped, every field XML-escaped);
`ONVIF_LOCATION` set to an empty value drops the location scope. Unset, upstream's values stay.
`app.py` sets the name to the copy's container name (`cams-<name>`, the same value as in the
rendered `compose.yml`), the manufacturer to `Moonie-Cams`, the model to `Cams`, and the location
to the camera's `location` (none by default). The serial number stays upstream's: the address the
client reached, unique per copy since each copy has its own IP, which clients use to tell cameras apart. A
proxied copy (`mode: proxy`) gets none of this: it relays the real camera's own identity.

The image starts go2rtc through `go2rtc/check-secrets.sh` (between upstream's `tini` and its
command, which are kept). The secret files hold the raw passwords. For every file `KEY` of
`$CREDENTIALS_DIRECTORY` the script exports `KEY_URL`: the value (trimmed of surrounding
whitespace, as go2rtc does) percent-encoded for a URL's account, every byte but `A-Z a-z 0-9 - . _ ~`
written `%XX`. `app.py` writes each `${KEY}` of a URL's account (`source`, `source_sub`,
`rtmp_target`, before the `@`) as `${KEY_URL}`, and go2rtc's URL parser decodes it, so a camera
password holding `@`, `^`, `:` or `/` reaches the camera as it is. Upstream pasted the raw value
into the URL, logged `net/url: invalid userinfo` and retried forever. go2rtc looks a `${NAME}` up
in its credential files first, then in the environment, which is where `KEY_URL` lives. The copy's
own RTSP account (`rtsp.password`, in proxy mode the camera account) keeps the raw `${KEY}`, so
clients type the real password. A `${KEY}` elsewhere in a URL (path, query) also stays raw, and
secret keys ending in `_URL` are refused.

go2rtc pastes the values into its config text before parsing it, so a value holding `"`, `\` or a
control character (a tab, an inner newline) would break the config wherever it is pasted raw.
go2rtc also trims Unicode spaces (no-break space, U+2000-U+200A, line and paragraph separators,
ideographic space, ...) that the script does not, so a secret holding one, or a C1 control
(U+0080-U+009F, U+0085 among them), is refused too. The script refuses those only: the copy exits
with one line naming the file, never its value:

```text
secret FRONT_SOURCE_PASSWORD holds a character go2rtc's config cannot hold (" \, a control character such as a tab or an inner newline, or a Unicode space such as a no-break space); change it
```

No binary or image is downloaded prebuilt. `go2rtc/Dockerfile` clones go2rtc at tag v1.9.15,
checks the full commit, applies every `go2rtc/*.patch` in sorted order (a patch that does not apply
fails the build; a new patch is just a new file there), runs the patched packages' Go tests (a
failing test fails the build), builds it with the pinned official Go image, and copies the
binary into the pinned upstream go2rtc image (ffmpeg and the rest unchanged). `app.py render` copies
`go2rtc/` next to the compose file, which builds it as `moonie-cams-go2rtc:1.9.15-p6`. The licence
and what the patch changes are in `go2rtc/NOTICE`.

`just go2rtc` builds the image (`just go2rtc linux/amd64` for an x86_64 host); `just onvif-check`
builds it and checks GetProfiles: the env values with no client, then the stream's own size and
frame rate while a client reads it, and the identity GetDeviceInformation and GetScopes announce.
It also starts go2rtc through `check-secrets.sh`, under the image's own busybox shell, with a
secret holding `"` and one holding a no-break space (both refused), then with a good one, and
proves a camera password holding `@ ^ : /` and UTF-8 reaches a second go2rtc playing the
camera through `${KEY_URL}`, while a wrong one gets no frame. It then tries to hijack the stream and
to make go2rtc dial `192.0.2.1` through the API, and checks each attempt is refused and the stream
is unchanged.

## Known limits

- No PTZ and no motion events through a plain copy: go2rtc's ONVIF server serves media only. Use
  `mode: proxy` for a camera that has its own ONVIF (a Tapo).
- What ONVIF advertises follows the real stream only while it runs and only for H.264 whose SDP
  carries its SPS; otherwise it is the camera's `size`, `fps` and `bitrate` (or go2rtc's 1080p30,
  8192 kbit/s). The bitrate is never measured.
- Proxy mode: no WS-Discovery (adopt by IP); PTZ clients are told apart by source IP, so two
  clients behind one address share the hold; events come through PullPoint subscriptions only,
  relayed to the real Tapo: push (`Subscribe`) is refused.
- Proxy mode: the proxy runs in the go2rtc container's network namespace, and Docker only joins it
  when the proxy starts. When go2rtc restarts after a crash it gets a new namespace; the proxy
  notices it can no longer reach go2rtc on localhost and exits, and Docker restarts it into the
  new one (about 15 seconds without ONVIF). `just proxy-check` proves it with a go2rtc stand-in
  that crashes once. If go2rtc is recreated rather than restarted, run `just up` again.
- State mirror: ARP is not filtered (nft `inet` sees IP only), so a closed copy still answers
  ARP for its address; nothing above ARP answers.
- One go2rtc container and one LAN IP per camera a client adds (a client may identify a camera by
  its address; UniFi Protect does).
- go2rtc's ONVIF server does not check credentials; the RTSP stream it points to does. It
  answers `SystemReboot` with an `ActionNotSupported` fault and never restarts
  (`go2rtc/onvif-profile.patch`; upstream exits the whole copy a second later).
- With macvlan, the Docker host itself cannot reach the copies (other machines can). Run the
  clients on another host, or add a macvlan shim interface on the host.
- Keep the host clock right (NTP): ONVIF clients may check the camera time.

## Licence

Copyright (C) 2026 LunaCreatoris. Moonie-Cams is free software under the GNU Affero General Public License, version 3 or (at your option)
any later version (SPDX: `AGPL-3.0-or-later`); the full text is in `LICENSE`.

The patched go2rtc it builds is [go2rtc](https://github.com/AlexxIT/go2rtc) by Alexey Khit, MIT licensed;
its licence and the changes made to it are in `go2rtc/NOTICE`.
