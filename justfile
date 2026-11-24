# Moonie-Cams: one go2rtc per camera, rendered from cameras.yaml.
# Every step runs in Docker, so the recipes behave the same on macOS, Linux and Windows.

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

tools := "moonie-cams-tools"
# Keep in step with GO2RTC_IMAGE in app.py (a test checks it)
go2rtc_image := "moonie-cams-go2rtc:1.9.15-p6"
run := "docker run --rm -v " + quote(justfile_directory()) + ":/work -w /work " + tools
compose := "docker compose --env-file .env -f generated/compose.yml"
netns := "docker compose -f " + quote(justfile_directory() / "tests" / "netns" / "compose.yml")
mirror := "docker compose -f " + quote(justfile_directory() / "tests" / "mirror" / "compose.yml")
webhook := "docker compose -f " + quote(justfile_directory() / "tests" / "webhook" / "compose.yml")

# List the recipes
default:
    @just --list

# Build the small Python tool image (render, lint, test)
tools:
    docker build -q -t {{tools}} .

# Lint and run every test
test: tools
    {{run}} sh -c "ruff check . && ruff format --check . && pytest"

# Test, then render the example and validate the result with docker compose
check: test
    {{run}} python app.py render cameras.example.yaml .check --allow-placeholders --env=.env.example
    docker compose --env-file .env.example -f .check/compose.yml config --quiet

# The merge gate, and the only CI: lint, every test, then render and validate the example
ci: check

# Render cameras.yaml into generated/ (one go2rtc config per camera, plus compose.yml)
render: tools
    {{run}} python app.py render cameras.yaml generated --env=.env

# Render, then start (or update) every camera copy
up: render
    {{compose}} up -d --remove-orphans

# Stop every camera copy
down:
    {{compose}} down

# Follow the logs (all, or one service: just logs cam-c100, just logs cams-webhook)
logs *service:
    {{compose}} logs -f --tail 100 {{service}}

# Scan the tracked files for your private words (PRIVATE_WORDS_FILE: your own list, kept outside the repo)
privacy-check: tools
    docker run --rm -v {{quote(justfile_directory())}}:/work -v {{quote(join(invocation_directory(), env_var("PRIVATE_WORDS_FILE")))}}:/run/private-words:ro -e PRIVATE_WORDS_FILE=/run/private-words -w /work {{tools}} pytest -rs tests/test_no_deployment_details.py

# Build the patched go2rtc image from source (optionally for one platform: just go2rtc linux/amd64)
go2rtc platform="":
    docker build {{ if platform == "" { "" } else { "--platform " + platform } }} -t {{go2rtc_image}} go2rtc

# Build the patched go2rtc, check its secret handling, ONVIF profile (env, then live), identity, and that the API cannot hijack a stream
onvif-check platform="": (go2rtc platform)
    docker run --rm {{ if platform == "" { "" } else { "--platform " + platform } }} -e ONVIF_WIDTH=1280 -e ONVIF_HEIGHT=720 -e ONVIF_FPS=10 -e ONVIF_BITRATE=600 -e ONVIF_NAME=cams-check -e ONVIF_MANUFACTURER=Moonie-Cams -e ONVIF_MODEL=Cams -e "ONVIF_LOCATION=Hall 2" -v {{quote(justfile_directory() / "tests" / "onvif")}}:/check:ro --entrypoint sh {{go2rtc_image}} /check/check.sh

# Check that the ONVIF proxy rejoins go2rtc's network namespace after go2rtc crashes and restarts
proxy-check:
    {{netns}} down --volumes --remove-orphans
    {{netns}} up -d --build
    {{netns}} run --rm probe
    {{netns}} down --volumes --remove-orphans

# Check that a copy goes dark while its camera is down (an established client connection too), comes back with it (source given by name), and still does after go2rtc restarts or stays down
mirror-check:
    {{mirror}} --profile client down --volumes --remove-orphans
    {{mirror}} up -d --build dns camera cam cam-mirror
    {{mirror}} run --rm client online
    {{mirror}} up -d held
    {{mirror}} kill camera
    {{mirror}} run --rm client offline
    {{mirror}} wait held
    {{mirror}} stop cam-mirror
    {{mirror}} run --rm client online 5
    {{mirror}} start cam-mirror
    {{mirror}} run --rm client offline 10
    {{mirror}} start camera
    {{mirror}} run --rm client online
    {{mirror}} restart -t 1 cam
    {{mirror}} kill camera
    {{mirror}} run --rm client offline 60
    {{mirror}} start camera
    {{mirror}} run --rm client online
    {{mirror}} stop cam
    -{{mirror}} wait cam-mirror
    {{mirror}} start cam
    {{mirror}} up -d cam-mirror
    {{mirror}} kill camera
    {{mirror}} run --rm client offline 60
    {{mirror}} start camera
    {{mirror}} run --rm client online
    {{mirror}} logs --no-log-prefix cam-mirror
    {{mirror}} --profile client down --volumes --remove-orphans

# Check webhook support: a camera goes down, then comes back, and a fake endpoint gets offline, then online (nothing at start)
webhook-check:
    {{webhook}} --profile check down --volumes --remove-orphans
    {{webhook}} up -d --build camera cam cam-mirror receiver
    {{webhook}} run --rm expect file online
    {{webhook}} up -d --build cams-webhook
    {{webhook}} run --rm expect quiet 6
    {{webhook}} kill camera
    {{webhook}} run --rm expect offline 60
    {{webhook}} start camera
    {{webhook}} run --rm expect online 60
    {{webhook}} logs --no-log-prefix cams-webhook
    {{webhook}} --profile check down --volumes --remove-orphans

# Health per output of one copy (mode: copy): source state, clients, kbit/s, drops (just health c200)
health name seconds="2":
    docker cp health.py cams-{{name}}:/tmp/health.py
    docker exec cams-{{name}} python3 /tmp/health.py {{seconds}}

copy_check := "docker compose -p moonie-cams-copy-check -f .copy-check/out/compose.yml -f tests/copy_check/compose.override.yml"

# Copy a test source to 2 RTSP, MJPEG and HLS clients; freeze and kill MJPEG, check the rest keep going
copy-check: tools
    {{run}} sh -c "python app.py render tests/copy_check/cameras.check.yaml .copy-check/out && mkdir -p .copy-check/secrets && printf copycheck > .copy-check/secrets/COPYCHECK_OUTPUT_PASSWORD"
    -{{copy_check}} down --remove-orphans
    {{copy_check}} up --build --abort-on-container-exit --exit-code-from checker
    {{copy_check}} down --remove-orphans
