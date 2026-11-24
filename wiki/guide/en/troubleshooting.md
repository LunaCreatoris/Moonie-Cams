---
title: Troubleshooting
description: The problems already met with real cameras, and what fixed them.
order: 6
---

## A Tapo ends the stream as soon as it is played {#tapo-stale-session}

**What you see**: the camera account is accepted, but the Tapo ends playback at once, and the copy's log shows an end
of stream (`EOF`) over and over.

**The cause**: an old session still holds `stream1` on the Tapo, for example that of a client that read it directly
before the copy. A Tapo accepts only one connection per stream.

**The fix**: remove every other client from the Tapo (Protect directly, the Tapo integration's camera entity), then
reboot the camera. Also check that its privacy mode is off.

## A password with special characters {#special-characters}

**What happened**: with a password holding `@`, `^`, `:` or `/`, go2rtc logged `invalid userinfo` and retried
forever.

**The fix**: this is solved. Cams now encodes the password in the source URL, and the camera receives it as it is.
Only `"`, `\`, control characters and Unicode spaces (such as a no-break space) are still refused: the copy stops
with one line naming the secret's file, never its value. Change that password then.

## A green picture below the first band {#green-picture}

**What happened**: Protect showed only the top of a re-encoded MJPEG stream correctly, and the rest of the picture
was green.

**The fix**: this is solved. Every config forces a Main-profile H.264 encoder with a single slice per frame: Protect
decoded only the first slice of the default encoder. If the picture stays green, run `just up` again to render the
copies afresh.

## Protect keeps old values {#protect-cached-values}

**What you see**: after a change of size, frame rate, bitrate or streams, Protect still shows the old values.

**The cause**: Protect keeps the profile values it saw at adoption.

**The fix**: remove the camera from Protect and adopt it again. See [Adopt in UniFi Protect](unifi-protect.md).

## More help {#more-help}

The known limits are in the
[Known limits](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#known-limits) section of the
README.
