---
title: Adopt in UniFi Protect
description: Adopt a copy in UniFi Protect by its IP address, and what Protect shows afterwards.
order: 2
---

## Before you adopt {#before-you-adopt}

- The copy needs an `onvif` output and an `ip` address on the same local network as the console.
- Keep the host clock right (NTP): ONVIF clients may check the camera time.
- For a Tapo in [proxy mode](tapo-proxy.md), first remove the Tapo from Protect if it was adopted directly: the copy
  holds the only RTSP session of each stream.

## Adopt by IP address {#adopt-by-ip}

1. In Protect, Settings, System, turn on "Discover Third-Party Cameras".
2. Choose "Advanced Adoption" and give the copy's address (ONVIF on port 80).
3. Give the copy's account:
    - in default or copy mode: the user `cams` and the password from `secrets/NAME_OUTPUT_PASSWORD`;
    - in proxy mode: the Tapo camera account.

Cams does no WS-Discovery: a copy does not always show up in the list on its own, but adoption by IP address always
works.

## What Protect shows {#what-protect-shows}

- **The name**: in default or copy mode, the copy announces itself as `cams-NAME`, manufacturer `Moonie-Cams`, model
  `Cams`, the same name `docker ps` and `just logs` show. In proxy mode, Protect sees the real Tapo's model and
  firmware, with the copy's address and MAC.
- **The streams**: each stream is one ONVIF profile. With `source_sub`, Protect gets its low-quality stream.
- **Size, frame rate and bitrate**: those of the real stream while it runs, otherwise the camera's `size`, `fps` and
  `bitrate` values. A re-encoded MJPEG stream is capped at 10 frames per second and 600 kbit/s by default.
- **Offline**: when the real camera goes down, the copy goes dark too, and Protect shows it disconnected rather than
  online with no picture. See [The state mirror](state-mirror.md).

Protect keeps the profile values it saw at adoption. If the streams, the size or the bitrate change, remove the
camera from Protect and adopt it again. See [Troubleshooting](troubleshooting.md#protect-cached-values).

## Home Assistant at the same time {#home-assistant}

A copy serves Home Assistant too:

- in default or copy mode, a Generic Camera or any RTSP integration on
  `rtsp://cams:PASSWORD@198.51.100.20:554/NAME` (`NAME_sub` for the small stream);
- in proxy mode, the ONVIF integration pointed at the copy. See [Share a Tapo](tapo-proxy.md).
