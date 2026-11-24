# go2rtc 1.9.15 built from source with the patches in this folder; onvif-profile.patch makes
# its ONVIF server advertise the stream's real size and frame rate (or the ONVIF_* env values)
# instead of a fixed 1080p30 at 8192 kbit/s and refuses SystemReboot, health-endpoint.patch
# adds a read-only /api/health (used by app.py's copy mode), and no-api-source.patch stops API
# queries from creating, re-pointing or pushing into a stream, and onvif-vendor-identity.patch
# makes its ONVIF server announce the ONVIF_NAME, ONVIF_MANUFACTURER, ONVIF_MODEL and
# ONVIF_LOCATION values instead of go2rtc's own. The runtime is the official image, with the
# binary replaced and check-secrets.sh run before go2rtc starts (secrets URL-encoded for URLs).

# Go version required by go2rtc's go.mod (go 1.26.0). The builder runs on the build host's
# platform and cross-compiles, so an amd64 image builds fast on an arm64 Mac and vice versa.
FROM --platform=$BUILDPLATFORM golang:1.26.11-bookworm@sha256:dc40a13b61304647ed7e5d581d246b86413dfc29d63c6d018310feb93cd98fc0 AS build
ARG TARGETOS TARGETARCH
ARG GO2RTC_TAG=v1.9.15
# The full commit the tag must point to, compared exactly: a moved tag fails the build
ARG GO2RTC_COMMIT=7c05066c90ab6ad1fccb506d5f0408aa31ebe60f
WORKDIR /src
RUN git clone --depth 1 --branch "$GO2RTC_TAG" https://github.com/AlexxIT/go2rtc.git . \
    && if [ "$(git rev-parse HEAD)" = "$GO2RTC_COMMIT" ]; then :; else echo "go2rtc $GO2RTC_TAG is not commit $GO2RTC_COMMIT" >&2; exit 1; fi
RUN go mod download
# Every go2rtc/*.patch is applied, in sorted (byte) order; any patch that does not apply
# cleanly fails the build. Add a patch by dropping a new file next to this one.
COPY *.patch /patches/
RUN set -e; ls /patches/*.patch > /dev/null; \
    for patch in $(ls /patches/*.patch | LC_ALL=C sort); do echo "apply $patch"; git apply "$patch"; done
# The patched packages run their tests (the patches' own included) on the build host, before
# the binary is built: a failing test fails the build. TestRecursion and TestTempate are
# upstream's and already fail on plain v1.9.15 (New refuses their sources, as no rtsp handler
# is registered in the test), and TestRecursion relies on GetOrPatch creating a stream, which
# no-api-source.patch removes on purpose; they are skipped by exact name.
RUN go test -count=1 -skip '^(TestRecursion|TestTempate)$' ./internal/streams/ ./pkg/h264/ ./pkg/onvif/ ./internal/mjpeg/ ./internal/webrtc/ ./internal/onvif/
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -ldflags "-s -w" -o /out/go2rtc .

# Pinned by tag and digest (multi-platform index, MIT, ships ffmpeg): the official runtime image.
FROM alexxit/go2rtc:1.9.15@sha256:e098de5c2f8040dc3d4784ed2d5cfdac3b0979f9349afe587ef6a670a14b9f4d
COPY --from=build /out/go2rtc /usr/local/bin/go2rtc
# Exports each secret URL-encoded as <KEY>_URL (refusing one that breaks the config), then execs
# go2rtc. The upstream image's entrypoint (tini) and command are kept; setting ENTRYPOINT resets
# CMD, so CMD is repeated.
# Run with sh: no executable bit is relied on.
COPY check-secrets.sh /usr/local/bin/check-secrets.sh
ENTRYPOINT ["/sbin/tini", "--", "sh", "/usr/local/bin/check-secrets.sh"]
CMD ["go2rtc", "-config", "/config/go2rtc.yaml"]
