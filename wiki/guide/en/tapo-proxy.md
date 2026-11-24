---
title: Share a Tapo
description: Proxy mode shares a Tapo between UniFi Protect and Home Assistant, pan-tilt and events included.
order: 3
---

## Why a proxy {#why-a-proxy}

A Tapo accepts only one connection per stream, and both Protect and Home Assistant want it. With `mode: proxy`, the
copy presents itself as the camera:

- go2rtc holds the single RTSP session of each stream (`stream1` and `stream2`), never re-encoded;
- a small ONVIF proxy relays reads, pan-tilt commands and events to the real Tapo, and rewrites the addresses so that
  they point to the copy.

Any other setting (reboot, network, encoder, image) is refused: it would change the camera for every client.

## Configure {#configure}

```yaml
cameras:
  - name: living_room
    input: rtsp
    source: rtsp://cams:${LIVING_ROOM_SOURCE_PASSWORD}@192.0.2.11:554/stream1
    source_sub: rtsp://cams:${LIVING_ROOM_SOURCE_PASSWORD}@192.0.2.11:554/stream2
    outputs: [onvif]
    ip: 198.51.100.22
    mode: proxy
    onvif_url: http://192.0.2.11:2020
    ptz_hold: 5
    proxy_log: writes
```

- `onvif_url` is the Tapo's ONVIF address, port 2020, with no account and no path.
- One account: clients give the copy the Tapo camera account, for ONVIF and RTSP alike.
- The proxy answers on ports 80 and 2020. In this mode, go2rtc's API (MJPEG, HLS, WebRTC) moves to port 1984.

## Protect and Home Assistant together {#protect-and-home-assistant}

1. Remove the Tapo from Protect if it was adopted directly, then adopt the copy by its address with the camera
   account. See [Adopt in UniFi Protect](unifi-protect.md).
2. In Home Assistant, add the ONVIF integration pointed at the copy (port 80 or 2020, camera account): it gets the
   streams, pan-tilt and the events.
3. The HACS Tapo integration may keep the real camera for its extra settings (privacy mode, alarm, LED), but turn off
   its stream or its camera entities: otherwise it takes the Tapo's only session.

## Pan-tilt arbitration {#ptz-arbitration}

Priority goes to the first who sends a command. The first client (by IP address) that moves the camera keeps control
until `ptz_hold` seconds (5 by default) after its last command.

- The other client's commands get a "PTZ is in use by another client" error rather than a fake success, so that its
  interface knows the camera did not move.
- Stop counts as a command: the other client cannot stop a move in progress.
- Reads (position, presets) are never blocked.

## The log {#logs}

The proxy writes one line per request:

```sh
just logs cam-living_room-onvif
```

```text
2026-11-23T10:00:01Z 198.51.100.5 /onvif/service ContinuousMove[pan=0.5 tilt=0 zoom=0 profile=profile_1] ptz-claim 200 41ms
2026-11-23T10:00:03Z 198.51.100.6 /onvif/service GotoPreset[preset=3 profile=profile_1] ptz-busy:holder=198.51.100.5,left=3.0s - 0ms
2026-11-23T10:00:04Z 198.51.100.6 /onvif/device_service SystemReboot refused:camera-command - 1ms
```

Each line gives the time, the client, the service, the operation (with what a pan-tilt command asks for), the
decision, the camera's answer and the duration. It never holds a password or a header. `proxy_log` picks how much is
written: `all` (everything), `writes` (the default: commands, refusals, pan-tilt and errors) or `off`.

The fields are detailed in the
[Proxy log](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#proxy-log) section of the README.
