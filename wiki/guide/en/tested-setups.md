---
title: Tested setups
description: The clients and sources actually tried with Cams, with their concrete steps.
order: 7
---

## What this page holds {#about}

The rest of the guide describes Cams in generic terms: any RTSP or MJPEG source, any ONVIF or RTSP client. This page
gathers the clients and sources that were actually tried with Cams, with the steps specific to each product.
Addresses and names are examples.

## UniFi Protect {#unifi-protect}

Protect adds a copy as a third-party ONVIF camera. The general steps are in [Add to a client](onvif-clients.md).

### Adopt a copy {#unifi-protect-adopt}

1. In Protect, Settings, System, turn on "Discover Third-Party Cameras".
2. Choose "Advanced Adoption" and give the copy's address (ONVIF on port 80).
3. Give the copy's account: the user `cams` and the password from `secrets/NAME_OUTPUT_PASSWORD`.

Cams does no WS-Discovery: a copy does not always show up in the list on its own, but adoption by IP address always
works.

### Adopt a proxied copy {#unifi-protect-adopt-proxied}

1. If the real camera was adopted directly in Protect, remove it first: the copy holds the only RTSP session of each
   stream.
2. Adopt the copy by its address, as above, with the real camera's account (for a Tapo, its camera account, see
   [Tapo](#tapo)).

### What Protect shows {#unifi-protect-what-it-shows}

- **The name**: in default or copy mode, the copy announces itself as `cams-NAME`, manufacturer `Moonie-Cams`, model
  `Cams`. In proxy mode, Protect sees the real camera's model and firmware, with the copy's address and MAC.
- **The streams**: each stream is one ONVIF profile. With `source_sub`, Protect gets its low-quality stream.
- **Offline**: when the real camera goes down, the copy goes dark too, and Protect shows it disconnected rather than
  online with no picture. See [The state mirror](state-mirror.md).

### Known quirks {#unifi-protect-quirks}

- **Kept values**: Protect keeps the profile values it saw at adoption. If the streams, the size, the frame rate or
  the bitrate change, remove the camera from Protect and adopt it again. See
  [Troubleshooting](troubleshooting.md#cached-profile-values).
- **A green picture**: Protect decoded only the first slice of the default H.264 encoder, and the bottom of a
  re-encoded MJPEG stream stayed green. This is solved: every config forces the Main profile, one slice per frame.
  If the picture stays green, run `just up` again. See [Troubleshooting](troubleshooting.md#green-picture).

## Home Assistant {#home-assistant}

A copy serves Home Assistant at the same time as the other clients.

### Read a copy over RTSP {#home-assistant-rtsp}

In default or copy mode, add a Generic Camera (or any RTSP integration) on
`rtsp://cams:PASSWORD@198.51.100.20:554/NAME`, `NAME_sub` for the small stream.

### Add a copy over ONVIF {#home-assistant-onvif}

In proxy mode, add the ONVIF integration pointed at the copy (port 80 or 2020) with the real camera's account: it
gets the streams, pan-tilt and the events. If another client is already moving the camera, Home Assistant's pan-tilt
commands get an error for the `ptz_hold` time. See [Pan-tilt arbitration](proxy-mode.md#ptz-arbitration).

## Tapo {#tapo}

Tapo cameras are the ones [proxy mode](proxy-mode.md) was written for.

### What to know {#tapo-facts}

- A Tapo accepts only one connection per stream: `stream1` for the main stream, `stream2` for the small one.
- Its ONVIF answers on port 2020.
- One account serves RTSP and ONVIF: the camera account created in the Tapo app, separate from the app's own
  account.

### Configure a Tapo in proxy mode {#tapo-configure}

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
```

Clients give the copy the Tapo's camera account, for ONVIF and RTSP alike.

### Share a Tapo between Protect and Home Assistant {#tapo-protect-and-home-assistant}

1. Remove the Tapo from Protect if it was adopted directly, then adopt the copy. See
   [Adopt a proxied copy](#unifi-protect-adopt-proxied).
2. In Home Assistant, add the ONVIF integration pointed at the copy. See
   [Add a copy over ONVIF](#home-assistant-onvif).
3. The HACS Tapo integration may keep the real camera for its extra settings (privacy mode, alarm, LED), but turn
   off its stream or its camera entities: otherwise it takes the Tapo's only session.

### A Tapo ends the stream as soon as it is played {#tapo-stale-session}

**What you see**: the camera account is accepted, but the Tapo ends playback at once, and the copy's log shows an end
of stream (`EOF`) over and over.

**The cause**: an old session still holds `stream1`, for example that of a client that read the Tapo directly before
the copy, or of the Tapo integration's camera entity.

**The fix**: remove every other client from the Tapo, then reboot it. Also check that its privacy mode is off: it
stops the stream. See [Troubleshooting](troubleshooting.md#stale-source-session).

## OctoPrint {#octoprint}

The webcam of a 3D printer running OctoPrint serves an MJPEG stream over HTTP. Cams turns it into an ONVIF camera.

### Configure {#octoprint-configure}

```yaml
cameras:
  - name: printer
    input: mjpeg
    source: http://192.0.2.30/webcam/?action=stream
    outputs: [onvif]
    ip: 198.51.100.21
```

- `source` is the address of the stream itself, `/webcam/?action=stream`, not that of OctoPrint's interface nor
  that of a still picture (`?action=snapshot`).
- The MJPEG stream is re-encoded to H.264 for the ONVIF output, at 10 frames per second and 600 kbit/s by default;
  `fps` and `bitrate` change these values.
- An `mjpeg` output is refused, except in [copy mode](copy-mode.md): it is already the input format.
