# Local, offline image: no runtime downloads, non-root, telemetry off.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SEMGREP_SEND_METRICS=off SEMGREP_ENABLE_VERSION_CHECK=0 AI_BOM_TELEMETRY=false \
    HOME=/tmp

# No git and no apt: the container scans mounted local repos only (commit read from .git directly).
RUN useradd --create-home --uid 10001 app

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY --chown=app . .
USER app

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health')"
# 0.0.0.0 inside the container only; compose publishes it on the host's 127.0.0.1.
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]
