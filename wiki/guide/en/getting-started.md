---
title: Getting started
description: Describe the cameras in cameras.yaml, store their passwords, render the stack, then start it with Docker Compose, as a Portainer stack, or with just.
order: 1
---

## What you need {#requirements}

- A Linux host with Docker and Docker Compose, on the local network of the clients that will read the copies.
- A free address on the local network for each copy, outside the DHCP range. Each copy has its own address and its
  own MAC address (a `macvlan` network), like a real camera.
- Nothing else: every step runs in Docker. [just](https://github.com/casey/just) is optional, a shortcut for the
  same steps.

## Two ways to deploy {#two-ways}

- **Docker Compose or Portainer, without just**: the repository, plain `docker compose` commands, and Portainer's
  web editor if you use Portainer. See [Render](#render), then [Start with Docker Compose](#docker-compose) or
  [As a Portainer stack](#portainer).
- **With just**: the same steps as recipes. See [With just](#render-and-start).

Both read the same files (`.env`, `cameras.yaml`, `secrets/`) and render the same folder, `generated/`. No image is
published on a registry: every image is built from the repository, on the Docker host. The first build of the
patched go2rtc image takes a few minutes.

## Get the repository {#get-the-repository}

On the Docker host, clone the repository, here into `/opt/moonie-cams` (any folder works):

```sh
git clone https://github.com/LunaCreatoris/Moonie-Cams.git /opt/moonie-cams
cd /opt/moonie-cams
```

Without git, download the ZIP from the repository page (Code, then Download ZIP) and unpack it there. Every command
below runs from that folder.

## The network {#network}

Copy `.env.example` to `.env` and give the host interface, the subnet and the gateway the copies take their address
on:

```sh
cp .env.example .env
```

On Windows, in PowerShell: `Copy-Item .env.example .env`. Then edit `.env` with any text editor.

With `macvlan`, the Docker host itself cannot reach the copies; other machines can. Run the clients on another host,
or add a `macvlan` shim interface on the host.

## Describe the cameras {#cameras}

Copy `cameras.example.yaml` to `cameras.yaml` (`cp cameras.example.yaml cameras.yaml`) and add one entry per
camera:

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
    source: http://192.0.2.30:8080/video.mjpg
    outputs: [onvif]
    ip: 198.51.100.21
```

- `name` is also the stream path: `rtsp://198.51.100.20:554/entrance`.
- `input` is the source format, `outputs` the formats served. An output cannot be the input format, except in
  [copy mode](copy-mode.md).
- `ip` is the copy's address, needed for ONVIF.
- An `mjpeg` source is the HTTP address of the MJPEG stream itself, not that of a page showing it. A 3D printer's
  webcam is described in [Tested setups](tested-setups.md#octoprint).

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

Write each file with a text editor (a trailing newline is fine), or from the shell:

```sh
printf '%s' 'the-camera-password' > secrets/ENTRANCE_SOURCE_PASSWORD
printf '%s' 'the-copy-password' > secrets/ENTRANCE_OUTPUT_PASSWORD
```

On Windows, in PowerShell: `Set-Content -NoNewline -Path secrets\ENTRANCE_SOURCE_PASSWORD -Value 'the-camera-password'`.
A password typed on a command line stays in the shell's history: clear it, or use the editor.

## Render {#render}

```sh
docker compose -f tools/compose.yml run --rm render
```

This builds the small tool image the first time, then writes into `generated/` one go2rtc config per camera and the
compose file. It needs Docker only, no Python, and runs on any system that has Docker. A camera that cannot work is
refused, by name, before anything starts.

Render again after every change to `cameras.yaml`, to the webhook settings of `.env`, or to the list of secret files.
A copy reads its go2rtc config when it starts: one whose camera changed but that keeps its old settings needs a
restart.

## Start with Docker Compose {#docker-compose}

```sh
docker compose --env-file .env -f generated/compose.yml up -d --build --remove-orphans
```

The first start builds the images, then every copy starts; the same command updates them after a new render.
`--env-file .env` is needed because `.env` sits at the root and the compose file in `generated/`.

- `docker compose --env-file .env -f generated/compose.yml logs -f` follows the logs of every copy; add
  `cam-entrance` for those of one.
- `docker compose --env-file .env -f generated/compose.yml restart cam-entrance` restarts one copy.
- `docker compose --env-file .env -f generated/compose.yml down` stops every copy.

## As a Portainer stack {#portainer}

Portainer deploys the rendered stack from its web editor. The files stay on the Docker host: the stack names them by
their path there.

On the Docker host, in `/opt/moonie-cams`, write `.env`, `cameras.yaml` and `secrets/` as above. Then render,
naming the files by their path on the host, and build the images there:

```sh
docker compose -f tools/compose.yml run --rm render cameras.yaml generated --env=.env --host-dir=/opt/moonie-cams/generated
docker compose --env-file .env -f generated/compose.yml build
```

The secrets are then looked for in `/opt/moonie-cams/secrets`, next to `generated/`.

In Portainer, on that host's environment: Stacks, Add stack. Name the stack as `COMPOSE_PROJECT_NAME` in `.env`
(`moonie-cams` by default), pick Web editor, and paste the content of `generated/compose.yml`. Under Environment
variables, load your `.env` (Load variables from .env file), or add `CAMS_PARENT`, `CAMS_SUBNET`, `CAMS_GATEWAY` and
`TZ` one by one, plus the webhook ones if you set them. Then Deploy the stack.

Portainer may log that a secret file does not exist: its compose looks for it inside Portainer's own container. The
copy still gets it from the host. `just webeditor-check` proves this way works, with a compose client that has
only the compose text.

To update: render and build again with the two commands above, then in Portainer open the stack, paste the new
`generated/compose.yml` in its editor, and Update the stack. The values of Environment variables must stay those of
the `.env` the render read.

Why not a stack from a Git repository:

- `generated/` is written by `app.py` from your `cameras.yaml`. Portainer cannot run that step, and both are kept
  out of the repository, since they describe your network.
- Portainer documents image builds from a Git stack as
  [not fully supported](https://docs.portainer.io/2.39-lts/faqs/troubleshooting/stacks-deployments-and-updates/can-i-build-an-image-while-deploying-a-stack-application-from-git),
  and since version 2.29.2
  [they fail](https://docs.portainer.io/faqs/known-issues/docker-compose-files-including-build-steps-fail) on an
  environment reached through an agent.
- Each copy mounts its config file, and the Community Edition does not mount files from the repository
  ([relative paths](https://docs.portainer.io/advanced/relative-paths) are a Business Edition feature).

## With just {#render-and-start}

```sh
just render
just up
```

`just render` runs the [render](#render) above. `just up` renders, builds the images the first time, then starts or
updates every copy.

- `just logs` follows the logs of every copy, `just logs cam-entrance` those of one.
- `just down` stops every copy.

Apart from that shortcut, just is the contributors' tool: `just check` checks the code and a render of the example,
and `just ci` is the check every change passes before it is merged.

## The same steps, both ways {#commands}

The other pages give the just recipe; here is the command it stands for.

| With just              | Without just                                                                                   |
|------------------------|------------------------------------------------------------------------------------------------|
| `just render`          | `docker compose -f tools/compose.yml run --rm render`                                          |
| `just up`              | render, then `docker compose --env-file .env -f generated/compose.yml up -d --build --remove-orphans` |
| `just logs cam-NAME`   | `docker compose --env-file .env -f generated/compose.yml logs -f --tail 100 cam-NAME`          |
| `just down`            | `docker compose --env-file .env -f generated/compose.yml down`                                 |
| `just health NAME`     | `docker cp health.py cams-NAME:/tmp/health.py`, then `docker exec cams-NAME python3 /tmp/health.py 2` |

In Portainer, the logs and the restart of a copy are on its container's page.

Next: [add the copies to a client](onvif-clients.md).
