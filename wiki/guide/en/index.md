---
title: Moonie Cams
description: Cams translates and copies your cameras' streams.
status: Version 1.0.1
github: https://github.com/LunaCreatoris/Moonie-Cams
order: 4
---

## What Cams does {#what-cams-does}

Cams translates and copies your cameras' streams. A stream comes in in one format and goes out in the ones you need:
ONVIF, RTSP, MJPEG, HLS, WebRTC or RTMP. A single connection to the real camera, shared by every client.

A camera that can only serve RTSP or MJPEG thus becomes an ONVIF camera, which an ONVIF client adds like any other, and its stream stays readable over RTSP,
MJPEG, HLS or WebRTC at the same time.

All the video work is done by [go2rtc](https://github.com/AlexxIT/go2rtc), with its ffmpeg. Each camera gets its own
go2rtc container and its own address on the local network. Cams only turns a `cameras.yaml` file into one config per camera and a compose file. Nothing is
recorded or stored.

## The three modes {#modes}

- **Default**: the camera goes out in other formats (ONVIF, RTSP, MJPEG, HLS, WebRTC, RTMP). An MJPEG source is
  re-encoded to H.264 for the outputs that need it.
- **Proxy**: for a camera that has its own ONVIF, the copy presents itself as the camera itself. Several clients
  share it, pan-tilt and events included. See [Proxy mode](proxy-mode.md).
- **Copy**: a plain splitter, one connection to the source copied to every output, each one isolated from the
  others. See [Copy mode](copy-mode.md).

In every mode, a copy goes dark while its camera is offline, and comes back with it. See
[The state mirror](state-mirror.md).

## The guide's pages {#pages}

- [Getting started](getting-started.md): describe the cameras, store the passwords, start the copies.
- [Add to a client](onvif-clients.md): adding by IP address, what the client sees, reading over RTSP.
- [Proxy mode](proxy-mode.md): share an ONVIF camera, pan-tilt arbitration and the log.
- [Copy mode](copy-mode.md): one stream shared by several clients.
- [The state mirror](state-mirror.md): a copy offline while its camera is.
- [Troubleshooting](troubleshooting.md): the problems already met and how they were fixed.
- [Tested setups](tested-setups.md): the clients and sources actually tried, step by step.

## The technical reference {#reference}

Every setting, the go2rtc patches, the limits and the checks are described in the
[repository's README](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md). What is planned next is in
the [roadmap](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/ROADMAP.md).
