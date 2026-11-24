# Roadmap

What is deferred, and why. Each item says what it is and the reason it waits, was kept as an idea or was dropped.

## Planned

- **Tapo `Renew` answered with 400 through the proxy.** Find out whether it is a Tapo quirk or the rewritten
  `wsa:To`: compare with UniFi Protect talking to a Tapo directly. PullPoint events still work meanwhile.

### Minor

- **XML-escape the remaining go2rtc ONVIF `r.Host` sinks** (GetCapabilities, GetServices, GetStreamUri,
  GetSnapshotUri), as the identity patch already does for its own fields.
- **Sentinel stop during a slow name resolution**: the sentinel can be killed before it reopens the copy. Use IP
  sources, or add a `stop_grace_period` or a resolver timeout.
- **go2rtc's default WebRTC STUN servers are public** (Google, Cloudflare): pin or empty them if the `webrtc` output is
  used.

## Ideas

- **Require the camera account on go2rtc's own ONVIF in default mode.** Today only RTSP checks it; ONVIF only
  describes the stream and can no longer change anything, so this is optional.

## Dropped

- **A low-quality sub stream for MJPEG cameras**: out of scope, no client needs it.
- **WS-Discovery**: third-party adoption is by IP, and discovery is not the project's aim.
