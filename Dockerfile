FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# App
WORKDIR /opt/akanji
COPY . /opt/akanji

# Python deps
RUN python3 -m venv .venv \
    && .venv/bin/pip install --upgrade pip \
    && .venv/bin/pip install -r requirements.txt

# Persistent data
RUN mkdir -p /opt/akanji/db /opt/akanji/logs
VOLUME ["/opt/akanji/db", "/opt/akanji/logs"]

# Healthcheck (optional)
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import os; os.path.exists('/opt/akanji/db/onisowo.db')" || exit 1

# Run
CMD [".venv/bin/python", "main.py"]
