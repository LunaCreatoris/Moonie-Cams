---
title: The state mirror
description: A copy goes dark while its camera is offline and comes back on its own with it.
order: 5
---

## Why {#why}

Without the mirror, a copy whose camera is off keeps answering ONVIF, and Protect shows it online with no picture.
With the mirror, while the real camera is offline, the copy looks like an unplugged camera: no ping, no ONVIF, no
RTSP. It comes back on its own with the camera.

## How it works {#how-it-works}

Every copy has a sentinel next to it, `cam-NAME-mirror`.

- Every 5 seconds, it opens a TCP connection to the camera and closes it at once, without opening a session: a Tapo
  allows only one per stream.
- After 3 failures in a row, the copy closes; after 2 successes in a row, it opens again.
- Closed, the copy ignores every inbound packet except those of the camera itself, so that the sentinel and go2rtc
  see it come back.
- The sentinel acts only in its copy's network, never on the host nor on another copy.

Each change of state is one log line:

```sh
just logs cam-NAME-mirror
```

## Turn it off {#opt-out}

Set `mirror_state: false` on a camera to keep its copy always online. A source with no host to probe (`ffmpeg:`,
`exec:`) needs it: `just render` says so.

## If a copy stays closed {#stuck-closed}

After a `docker compose stop` then `start` of a copy, its sentinel may stay stopped. Run `just up`, which starts it
again and brings it up to date.

## An alert on each change {#webhook}

The optional `cams-webhook` service (container `<project>-cams-webhook-1`, `moonie-cams-cams-webhook-1` by default)
sends a JSON POST to the address of your choice (n8n, Home Assistant or any other receiver) each time a camera goes
offline or comes back online. Its setup is in the
[Webhook support](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#webhook-support) section of the
README.

The details are in the
[State mirror](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#state-mirror-a-copy-is-down-while-its-camera-is)
section of the README.
