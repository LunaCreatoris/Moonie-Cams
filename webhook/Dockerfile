# Webhook service of a compose project: one Python stdlib file, posting the sentinels' state changes.
# Same pinned base as sentinel/Dockerfile (a test checks it): a moved tag never changes what is built
FROM python:3.13.18-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY webhook.py /app/webhook.py
# No privilege at all: compose drops every capability and mounts the root read-only
# (app.py WEBHOOK_HARDENING); the state volume is mounted read-only too
USER 65534:65534
CMD ["python", "/app/webhook.py"]
