# ONVIF proxy of a camera copy: one Python file, run next to its go2rtc container.
# Pinned by tag and digest (multi-platform index): a moved tag never changes what is built
FROM python:3.13.18-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt /tmp/requirements.txt
# Every package is checked against its pinned hash: a re-uploaded or tampered file fails the build
RUN pip install --no-cache-dir --no-deps --require-hashes -r /tmp/requirements.txt
COPY onvif_proxy.py /app/onvif_proxy.py
# Root only to listen on port 80 like a camera; compose drops every capability but that one and
# mounts the root read-only (app.py PROXY_HARDENING)
CMD ["python", "/app/onvif_proxy.py"]
