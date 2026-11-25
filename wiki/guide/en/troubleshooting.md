---
title: Troubleshooting
description: The problems already met with real cameras, and what fixed them.
order: 6
---

## The camera ends the stream as soon as it is played {#stale-source-session}

**What you see**: the camera's account is accepted, but it ends playback at once, and the copy's log shows an end
of stream (`EOF`) over and over.

**The cause**: an old session still holds the stream on the camera, for example that of a client that read it
directly before the copy. A camera that accepts only one connection per stream then refuses the copy.

**The fix**: remove every other client that reads the camera directly, then reboot it. Also check that no privacy
mode of the camera cuts its stream. A real case is described in [Tested setups](tested-setups.md#tapo-stale-session).

## A password with special characters {#special-characters}

**What happened**: with a password holding `@`, `^`, `:` or `/`, go2rtc logged `invalid userinfo` and retried
forever.

**The fix**: this is solved. Cams now encodes the password in the source URL, and the camera receives it as it is.
Only `"`, `\`, control characters and Unicode spaces (such as a no-break space) are still refused: the copy stops
with one line naming the secret's file, never its value. Change that password then.

## A green picture below the first band {#green-picture}

**What happened**: a client showed only the top of a re-encoded MJPEG stream correctly, and the rest of the picture
was green.

**The fix**: this is solved. Every config forces a Main-profile H.264 encoder with a single slice per frame: some
decoders read only the first slice of the default encoder. If the picture stays green, run `just up` again to render
the copies afresh. See also [Tested setups](tested-setups.md#unifi-protect-quirks).

## The client keeps old values {#cached-profile-values}

**What you see**: after a change of size, frame rate, bitrate or streams, the client still shows the old values.

**The cause**: some ONVIF clients keep the profile values they saw when the camera was added.

**The fix**: remove the camera from the client and add it again. See [Add to a client](onvif-clients.md).

## More help {#more-help}

The known limits are in the
[Known limits](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#known-limits) section of the
README.
