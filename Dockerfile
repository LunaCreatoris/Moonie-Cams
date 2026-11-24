# Tool image: renders cameras.yaml, lints and tests app.py. The cameras themselves run the go2rtc image.
# Pinned by tag and digest (multi-platform index): a moved tag never changes what is built
FROM python:3.13.18-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# busybox: tests/test_check_secrets.py runs go2rtc/check-secrets.sh under busybox ash too, the
# go2rtc image's shell (Alpine ships the same 1.37.0 release). git: tests/test_no_deployment_details.py
# scans the tracked files (git ls-files). A moved version fails the build.
RUN apt-get update && apt-get install -y --no-install-recommends busybox=1:1.37.0-6+b9 git=1:2.47.3-0+deb13u1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
COPY proxy/requirements.txt /tmp/proxy/requirements.txt
RUN pip install --no-cache-dir --no-deps -r /tmp/requirements.txt
WORKDIR /work
