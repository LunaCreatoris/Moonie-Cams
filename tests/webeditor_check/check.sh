#!/bin/sh
# Copyright (C) 2026 LunaCreatoris
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Run by `just webeditor-check` in a Docker CLI container, against the host's Docker. It deploys a
# rendered stack the way a web editor (such as Portainer's) does: the compose client has the
# compose text only, none of the rendered files.
# 1. The rendered folder lives on the Docker host: a volume, rendered in place with --host-dir set
#    to its path there (the volume's mountpoint), the secret next to it, as the guide describes.
# 2. The images are built there, from the rendered folder, as a host's `docker compose build` does.
# 3. The compose text alone is deployed from an empty folder: compose resolves no file itself.
# 4. The copy loaded its config (its stream is listed by /api/health) and its secret.
set -eu

project=moonie-cams-webeditor-check
volume=$project
tools=${TOOLS:?TOOLS names the tool image}
cli=${CLI_IMAGE:?CLI_IMAGE names this Docker CLI image}
secret=webcheck-secret

dir=$(docker volume inspect -f '{{.Mountpoint}}' "$volume")
empty=$(mktemp -d)
cleanup() {
    (cd "$empty" && docker compose -p "$project" -f compose.yml down --remove-orphans >/dev/null 2>&1) || true
    docker volume rm -f "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# 1. The recipe copied the app into the volume (src/): rendered into generated/, the secret in secrets/
docker run --rm -v "$volume:$dir" -w "$dir/src" "$tools" sh -c "python app.py render tests/webeditor_check/cameras.check.yaml $dir/generated --host-dir=$dir/generated >/dev/null && mkdir -p $dir/secrets && printf %s $secret > $dir/secrets/WEBCHECK_OUTPUT_PASSWORD"

# 2. Built on the host, from the rendered folder
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$volume:$dir" -w "$dir/generated" "$cli" docker compose -p "$project" build --quiet

# 3. Only the compose text reaches the client
docker run --rm -v "$volume:$dir:ro" "$tools" cat "$dir/generated/compose.yml" > "$empty/compose.yml"
cd "$empty"
TZ=Etc/UTC docker compose -p "$project" -f compose.yml up -d

# 4. The config file and the secret, read inside the copy
tries=0
until health=$(docker exec cams-webcheck curl -fsS http://127.0.0.1/api/health 2>/dev/null); do
    tries=$((tries + 1))
    if [ "$tries" -ge 30 ]; then
        docker logs cams-webcheck >&2 || true
        echo "FAIL the copy never served /api/health" >&2
        exit 1
    fi
    sleep 1
done
case $health in
    *webcheck_mjpeg*) ;;
    *) echo "FAIL /api/health does not list the rendered streams: $health" >&2; exit 1 ;;
esac
if [ "$(docker exec cams-webcheck cat /run/secrets/WEBCHECK_OUTPUT_PASSWORD)" != "$secret" ]; then
    echo "FAIL the copy did not get its secret from the host path" >&2
    exit 1
fi
echo "OK a stack pasted into a web editor runs: config file and secret come from the host"
