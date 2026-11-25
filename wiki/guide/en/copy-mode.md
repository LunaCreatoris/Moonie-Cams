---
title: Copy mode
description: Share a source's only connection between several clients, each output isolated from the others.
order: 4
---

## What it is for {#purpose}

`mode: copy` turns the copy into a plain splitter: one connection to the source, copied to every output at once, the
input format included, passed through untouched. It is useful for a source that allows one client, when several
clients want a copy of it.

```yaml
cameras:
  - name: workshop
    mode: copy
    input: rtsp
    source: rtsp://cams:${WORKSHOP_SOURCE_PASSWORD}@192.0.2.12:554/stream1
    outputs: [rtsp, mjpeg]
    ip: 198.51.100.23
```

Every RTSP client reads `rtsp://cams:PASSWORD@198.51.100.23:554/workshop` (one RTSP output, any number of
clients), and a browser reads `http://198.51.100.23/api/stream.mjpeg?src=workshop_mjpeg`.

## What each output gets {#what-each-output-gets}

- **Its own queue**: a late client drops its own frames and never holds the others back.
- **Its own ffmpeg**: every output that re-encodes reads its own stream, `NAME_OUTPUT`. An encoder that crashes only
  hits its own output.
- **Its own restart**: a client that leaves is removed alone, and the source connection stays open while any client
  is left.

## Follow its health {#health}

```sh
just health workshop
```

The recipe prints, for each stream, the source state, the clients, the bitrate and the drops. It never shows an
address, a URL or a secret.

## Limits {#limits}

- With an `onvif` output, each output's streams also show up as ONVIF profiles: pick the main stream in the client.
- Copy and proxy modes do not combine.

The details are in the
[Copy mode](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#copy-mode) section of the README.
