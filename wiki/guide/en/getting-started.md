---
title: Getting started
description: Describe the cameras in cameras.yaml, store their passwords, then render and start one copy per camera.
order: 1
---

## What you need {#requirements}

- A Linux host with Docker and Docker Compose, on the local network of the clients that will read the copies.
- [just](https://github.com/casey/just): every step is a recipe, and every recipe runs in Docker.
- A free address on the local network for each copy, outside the DHCP range. Each copy has its own address and its
  own MAC address (a `macvlan` network), like a real camera.

## The network {#network}

Copy `.env.example` to `.env` and give the host interface, the subnet and the gateway the copies take their address
on.

With `macvlan`, the Docker host itself cannot reach the copies; other machines can. Run the clients on another host,
or add a `macvlan` shim interface on the host.

## Describe the cameras {#cameras}

Copy `cameras.example.yaml` to `cameras.yaml` and add one entry per camera:

```yaml
cameras:
  - name: entrance
    input: rtsp
    source: rtsp://cams:${ENTRANCE_SOURCE_PASSWORD}@192.0.2.10:554/stream1
    source_sub: rtsp://cams:${ENTRANCE_SOURCE_PASSWORD}@192.0.2.10:554/stream2
    outputs: [onvif, mjpeg]
    ip: 198.51.100.20

  - name: garage
    input: mjpeg
    source: http://192.0.2.30:8080/?action=stream
    outputs: [onvif]
    ip: 198.51.100.21
```

- `name` is also the stream path: `rtsp://198.51.100.20:554/entrance`.
- `input` is the source format, `outputs` the formats served. An output cannot be the input format, except in
  [copy mode](copy-mode.md).
- `ip` is the copy's address, needed for ONVIF.

Every key is described in the
[Add a camera](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#add-a-camera) section of the README.

## The passwords {#secrets}

A password is never written in `cameras.yaml`: write `${KEY}` there, and put the raw password in the file
`secrets/KEY`, alone, without quotes.

- `secrets/ENTRANCE_SOURCE_PASSWORD`: the password the copy gives the real camera, that of its account.
- `secrets/ENTRANCE_OUTPUT_PASSWORD`: the password of the copy's RTSP account (user `cams`), which the clients give the
  copy.

The characters `@`, `^`, `:` and `/` are accepted. Only `"`, `\`, control characters and Unicode spaces (such as a
no-break space) are refused: the copy then stops with one line naming the file, never its value.

## Render and start {#render-and-start}

```sh
just render
just up
```

`just render` writes one go2rtc config per camera and the compose file into `generated/`. `just up` renders, builds
the go2rtc image the first time, then starts or updates every copy.

- `just logs` follows the logs of every copy, `just logs cam-entrance` those of one.
- `just down` stops every copy.
- `just check` checks the code and a render of the example.

Next: [add the copies to a client](onvif-clients.md).
