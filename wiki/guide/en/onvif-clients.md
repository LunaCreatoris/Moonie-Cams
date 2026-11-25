---
title: Add to a client
description: Add a copy to an ONVIF client by its IP address, what the client sees afterwards, and read its stream over RTSP.
order: 2
---

## Before you add {#before-you-add}

- The copy needs an `onvif` output and an `ip` address on the same local network as the client.
- Keep the host clock right (NTP): ONVIF clients may check the camera time.
- In [proxy mode](proxy-mode.md), first remove the real camera from every client that read it directly: the copy
  holds the only RTSP session of each stream.

## Add by IP address {#add-by-ip}

1. In the client, add an ONVIF camera by its address: the copy's, ONVIF on port 80.
2. Give the copy's account:
    - in default or copy mode: the user `cams` and the password from `secrets/NAME_OUTPUT_PASSWORD`;
    - in proxy mode: the real camera's account.

Cams does no WS-Discovery: a copy does not always show up in the client's search on its own, but adding by IP
address always works.

## What the client sees {#what-the-client-sees}

- **The name**: in default or copy mode, the copy announces itself as `cams-NAME`, manufacturer `Moonie-Cams`, model
  `Cams`, the same name `docker ps` and `just logs` show. In proxy mode, the client sees the real camera's model and
  firmware, with the copy's address and MAC.
- **The streams**: each stream is one ONVIF profile. With `source_sub`, the client also gets a low-quality stream.
- **Size, frame rate and bitrate**: those of the real stream while it runs, otherwise the camera's `size`, `fps` and
  `bitrate` values. A re-encoded MJPEG stream is capped at 10 frames per second and 600 kbit/s by default.
- **Offline**: when the real camera goes down, the copy goes dark too, and the client sees it disconnected rather
  than online with no picture. See [The state mirror](state-mirror.md).

Some clients keep the profile values they saw when the camera was added. If the streams, the size or the bitrate
change, remove the camera from the client and add it again. See
[Troubleshooting](troubleshooting.md#cached-profile-values).

## Read the stream over RTSP {#read-over-rtsp}

An RTSP client reads the copy without going through ONVIF:

- in default or copy mode, `rtsp://cams:PASSWORD@198.51.100.20:554/NAME` (`NAME_sub` for the small stream);
- in proxy mode, with the real camera's account, or over ONVIF to get pan-tilt and events too. See
  [Proxy mode](proxy-mode.md).
