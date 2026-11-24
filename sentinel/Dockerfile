# State sentinel of a camera copy: one Python file and nft, run in the copy's network namespace.
# Pinned by tag and digest (multi-platform index): a moved tag never changes what is built
FROM python:3.13.18-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# nft only; a moved Debian package version fails the build instead of changing what is installed
RUN apt-get update && apt-get install -y --no-install-recommends nftables=1.1.3-1 \
    && rm -rf /var/lib/apt/lists/*
COPY sentinel.py /app/sentinel.py
# Root only for nft; compose drops every capability but NET_ADMIN and mounts the root read-only
# (app.py SENTINEL_HARDENING)
CMD ["python", "/app/sentinel.py"]
