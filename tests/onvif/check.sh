#!/bin/sh
# Runs inside the patched go2rtc image (`just onvif-check`), started with
# ONVIF_WIDTH=1280 ONVIF_HEIGHT=720 ONVIF_FPS=10 ONVIF_BITRATE=600.
# 0. check-secrets.sh, the image's entrypoint, under the image's own busybox sh: a secret holding
#    '"', or a no-break space (go2rtc would trim it, the script would not), stops the start with
#    one line naming the file; then go2rtc itself is started through it, with a good secret.
# 1. No client: the stream is not running, so GetProfiles answers the env values.
# 2. A client reads the stream: GetProfiles answers the stream's own 320x240 at 5 fps,
#    and the bitrate, which has no live source, stays the env value.
# 3. no-api-source.patch: an API query cannot create, patch or push into a stream. Each
#    attempt is refused, no new stream appears, the attacker's host is never recorded,
#    and the config stream still serves its own 320x240 picture. A WHEP offer that sends video
#    (which upstream turned into a producer) is refused the same way.
# 4. onvif-profile.patch: SystemReboot gets a SOAP fault and go2rtc keeps running.
# 5. onvif-vendor-identity.patch, with ONVIF_NAME=cams-check ONVIF_MANUFACTURER=Moonie-Cams
#    ONVIF_MODEL=Cams ONVIF_LOCATION="Hall 2": GetDeviceInformation and GetScopes announce them.
# 6. A camera password holding @ ^ : / (and more, UTF-8 included) reaches the camera: a second
#    go2rtc plays the camera, its RTSP account holding the raw password, and a third reads it,
#    started through check-secrets.sh, with rtsp://check:${CHECK_SOURCE_PASSWORD_URL}@<own IP>/cam
#    (go2rtc skips the RTSP account on loopback, hence the container's own IP). A wrong password
#    gets no frame.
set -eu

secrets=$(mktemp -d)
printf 'bad"value' > "$secrets/CHECK_SOURCE_PASSWORD"
if out=$(CREDENTIALS_DIRECTORY=$secrets sh /usr/local/bin/check-secrets.sh go2rtc -config /check/go2rtc.yaml 2>&1); then
    echo "FAIL check-secrets.sh started go2rtc with a secret holding a double quote" >&2
    exit 1
fi
want='secret CHECK_SOURCE_PASSWORD holds a character go2rtc'"'"'s config cannot hold (" \, a control character such as a tab or an inner newline, or a Unicode space such as a no-break space); change it'
[ "$out" = "$want" ] || { echo "FAIL check-secrets.sh said '$out', want '$want'" >&2; exit 1; }
echo "ok   bad secret refused: $out"
printf 'bad\302\240value' > "$secrets/CHECK_SOURCE_PASSWORD"
if out=$(CREDENTIALS_DIRECTORY=$secrets sh /usr/local/bin/check-secrets.sh go2rtc -config /check/go2rtc.yaml 2>&1); then
    echo "FAIL check-secrets.sh started go2rtc with a secret holding a no-break space" >&2
    exit 1
fi
[ "$out" = "$want" ] || { echo "FAIL check-secrets.sh said '$out', want '$want'" >&2; exit 1; }
echo "ok   no-break space refused: $out"
# The camera password of step 6, raw, as an operator writes it
printf 'p@ss^wo:rd/1 %%#?[] Zone \316\251 \303\234\n' > "$secrets/CHECK_SOURCE_PASSWORD"

CREDENTIALS_DIRECTORY=$secrets sh /usr/local/bin/check-secrets.sh go2rtc -config /check/go2rtc.yaml &
go2rtc_pid=$!

profiles() {
    curl -fsS -X POST -H 'Content-Type: application/soap+xml' \
        --data '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/></s:Body></s:Envelope>' \
        http://127.0.0.1:1984/onvif/media_service
}

# The advertised profile as "WIDTHxHEIGHT FPS BITRATE"
values() {
    body=$(profiles) || return 1
    tag() { echo "$body" | sed -n "s|.*<tt:$1>\([0-9]*\)</tt:$1>.*|\1|p" | head -n 1; }
    echo "$(tag Width)x$(tag Height) $(tag FrameRateLimit) $(tag BitrateLimit)"
}

expect() {
    label=$1 want=$2 got=""
    i=0
    while [ $i -lt 30 ]; do
        got=$(values 2>/dev/null) || got=""
        [ "$got" = "$want" ] && { echo "ok   $label: $got"; return 0; }
        i=$((i + 1))
        sleep 1
    done
    echo "FAIL $label: want '$want', got '$got'" >&2
    exit 1
}

expect "fallback (no client, env)" "1280x720 10 600"

device() {
    curl -fsS -X POST -H 'Content-Type: application/soap+xml' \
        --data "<s:Envelope xmlns:s=\"http://www.w3.org/2003/05/soap-envelope\"><s:Body><tds:$1 xmlns:tds=\"http://www.onvif.org/ver10/device/wsdl\"/></s:Body></s:Envelope>" \
        http://127.0.0.1:1984/onvif/device_service
}
info=$(device GetDeviceInformation)
field() { echo "$info" | sed -n "s|.*<tds:$1>\([^<]*\)</tds:$1>.*|\1|p"; }
got="$(field Manufacturer) / $(field Model) / $(field SerialNumber)"
[ "$got" = "Moonie-Cams / Cams / 127.0.0.1:1984" ] || { echo "FAIL device information: '$got'" >&2; exit 1; }
echo "ok   device information: $got"
got=$(device GetScopes | sed -n 's|.*<tt:ScopeItem>\([^<]*\)</tt:ScopeItem>.*|\1|p' | tr '\n' ' ')
want="onvif://www.onvif.org/name/cams-check onvif://www.onvif.org/location/Hall%202 onvif://www.onvif.org/Profile/Streaming onvif://www.onvif.org/type/Network_Video_Transmitter "
[ "$got" = "$want" ] || { echo "FAIL scopes: '$got', want '$want'" >&2; exit 1; }
echo "ok   scopes: $got"

ffmpeg -hide_banner -loglevel error -rtsp_transport tcp -i rtsp://127.0.0.1:8554/cam -t 60 -f null - &
expect "live (client reading, SPS)" "320x240 5 600"

# HTTP status of one API request: code METHOD PATH [CURL-ARGS...]
code() {
    method=$1 path=$2
    shift 2
    curl -sS -o /dev/null -w '%{http_code}' -X "$method" --max-time 20 "$@" "http://127.0.0.1:1984$path"
}

# refused LABEL STATUS METHOD PATH [CURL-ARGS...]
refused() {
    label=$1 want=$2 method=$3 path=$4
    shift 4
    got=$(code "$method" "$path" "$@") || got="error"
    [ "$got" = "$want" ] || { echo "FAIL $label: $method $path answered $got, want $want" >&2; exit 1; }
    echo "ok   $label: $method $path -> $got"
}

evil="rtsp://192.0.2.1/x"
refused "hijack via frame" 404 GET "/api/frame.jpeg?src=$evil&name=cam"
refused "new stream via frame" 404 GET "/api/frame.jpeg?src=$evil"
refused "hijack via mjpeg" 404 GET "/api/stream.mjpeg?src=$evil&name=cam"
refused "new stream via mp4" 404 GET "/api/stream.mp4?src=$evil"
refused "new stream via hls" 404 GET "/api/stream.m3u8?src=$evil"
refused "push via mjpeg" 403 POST "/api/stream.mjpeg?dst=cam"
refused "push via whip" 403 POST "/api/webrtc?dst=cam"
# A client offering to send video (sendonly), on the play path src=
fp=AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89
whep=$(mktemp)
printf 'v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\nc=IN IP4 0.0.0.0\r\na=mid:0\r\na=sendonly\r\na=ice-ufrag:checkufrag\r\na=ice-pwd:checkpasswordcheckpassword\r\na=fingerprint:sha-256 %s\r\na=setup:actpass\r\na=rtcp-mux\r\na=rtpmap:96 H264/90000\r\na=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f\r\n' "$fp" > "$whep"
refused "push via whep" 403 POST "/api/webrtc?src=cam" -H 'Content-Type: application/sdp' --data-binary "@$whep"

names=$(curl -fsS http://127.0.0.1:1984/api/health | grep -o '"[^"]*":{"producers"' | cut -d '"' -f 2 | tr '\n' ' ')
[ "$names" = "cam " ] || { echo "FAIL streams after the attempts: '$names', want 'cam '" >&2; exit 1; }
echo "ok   streams after the attempts: $names"
if curl -fsS http://127.0.0.1:1984/api/streams | grep -q 192.0.2.1; then
    echo "FAIL the attacker's host reached a stream" >&2
    exit 1
fi
echo "ok   no stream points at 192.0.2.1"
reboot='<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><tds:SystemReboot xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/></s:Body></s:Envelope>'
refused "reboot via onvif" 500 POST "/onvif/device_service" -H 'Content-Type: application/soap+xml' --data-binary "$reboot"
# Upstream exits one second after a reboot request
sleep 2
kill -0 "$go2rtc_pid" 2>/dev/null || { echo "FAIL go2rtc exited after SystemReboot" >&2; exit 1; }
echo "ok   go2rtc still running after SystemReboot"
refused "config stream still serves" 200 GET "/api/frame.jpeg?src=cam"
expect "live after the attempts" "320x240 5 600"

# 6. The camera: its RTSP account holds the raw password (go2rtc reads the file itself)
ip=$(hostname -i | cut -d ' ' -f 1)
case $ip in 127.* | "") echo "FAIL no address besides loopback: '$ip'" >&2; exit 1 ;; esac
work=$(mktemp -d)
cat > "$work/camera.yaml" <<'YAML'
streams:
  cam:
    - "exec:ffmpeg -hide_banner -loglevel error -re -f lavfi -i testsrc=size=320x240:rate=5 -c:v libx264 -r 5 -g 10 -bf 0 -profile:v main -preset:v superfast -tune:v zerolatency -pix_fmt:v yuv420p -f rtsp {output}"
api: {listen: ""}
rtsp: {listen: ":8556", username: "check", password: "${CHECK_SOURCE_PASSWORD}"}
webrtc: {listen: ""}
srtp: {listen: ""}
log: {format: text, level: warn}
YAML
cat > "$work/copy.yaml" <<YAML
streams:
  good: ["rtsp://check:\${CHECK_SOURCE_PASSWORD_URL}@$ip:8556/cam"]
  wrong: ["rtsp://check:wrong@$ip:8556/cam"]
api: {listen: "127.0.0.1:1986"}
rtsp: {listen: ""}
webrtc: {listen: ""}
srtp: {listen: ""}
log: {format: text, level: error}
YAML
CREDENTIALS_DIRECTORY=$secrets sh /usr/local/bin/check-secrets.sh go2rtc -config "$work/camera.yaml" &
CREDENTIALS_DIRECTORY=$secrets sh /usr/local/bin/check-secrets.sh go2rtc -config "$work/copy.yaml" &
# "STATUS BYTES" of a snapshot; go2rtc answers a failed one with an empty 200, so both count
frame() { curl -sS -o /dev/null -w '%{http_code} %{size_download}' --max-time 20 "http://127.0.0.1:1986/api/frame.jpeg?src=$1" || true; }
jpeg() { case $1 in "200 0" | "200 ") return 1 ;; "200 "*) return 0 ;; *) return 1 ;; esac; }
got="" i=0
while [ $i -lt 30 ]; do
    got=$(frame good)
    jpeg "$got" && break
    i=$((i + 1))
    sleep 1
done
jpeg "$got" || { echo "FAIL the camera refused the URL-encoded password: frame answered '$got'" >&2; exit 1; }
echo "ok   password with @ ^ : / and UTF-8 reaches the camera through \${CHECK_SOURCE_PASSWORD_URL}: frame -> $got bytes"
got=$(frame wrong)
if jpeg "$got"; then echo "FAIL the camera accepted a wrong password: frame -> $got" >&2; exit 1; fi
echo "ok   a wrong password gets no frame: frame -> $got"
