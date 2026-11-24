---
title: Moonie Cams
description: Turns every camera, a Tapo, an ESP32-CAM or an OctoPrint webcam, into an ONVIF camera that UniFi Protect adopts and Home Assistant reads at the same time.
github: https://github.com/LunaCreatoris/Moonie-Cams
order: 4
---

## What Cams does {#what-cams-does}

A camera stream comes in in one format and goes out in others. The main use: make any camera look like an ONVIF
camera that UniFi Protect can adopt, while giving Home Assistant its RTSP stream.

All the video work is done by [go2rtc](https://github.com/AlexxIT/go2rtc), with its ffmpeg. Each camera gets its own
go2rtc container, its own address on the local network, and one single connection to the real camera, shared by
every client. Cams only turns a `cameras.yaml` file into one config per camera and a compose file. Nothing is
recorded or stored.

## The three modes {#modes}

- **Default**: the camera goes out in other formats (ONVIF, RTSP, MJPEG, HLS, WebRTC, RTMP). An MJPEG source
  (ESP32-CAM, OctoPrint) is re-encoded to H.264 for Protect.
- **Proxy**: for a Tapo, the copy presents itself as the camera itself. Protect and Home Assistant share it, pan-tilt
  and events included. See [Share a Tapo](tapo-proxy.md).
- **Copy**: a plain splitter, one connection to the source copied to every output, each one isolated from the
  others. See [Copy mode](copy-mode.md).

In every mode, a copy goes dark while its camera is offline, and comes back with it. See
[The state mirror](state-mirror.md).

## The guide's pages {#pages}

- [Getting started](getting-started.md): describe the cameras, store the passwords, start the copies.
- [Adopt in UniFi Protect](unifi-protect.md): adoption by IP address and what Protect shows.
- [Share a Tapo](tapo-proxy.md): proxy mode, pan-tilt arbitration and the log.
- [Copy mode](copy-mode.md): one stream shared by several clients.
- [The state mirror](state-mirror.md): a copy offline while its camera is.
- [Troubleshooting](troubleshooting.md): the problems already met and how they were fixed.

## The technical reference {#reference}

Every setting, the go2rtc patches, the limits and the checks are described in the
[repository's README](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md). What is planned next is in
the [roadmap](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/ROADMAP.md).
