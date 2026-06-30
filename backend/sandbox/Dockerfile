# ============================================================
# Sandbox image — isolated execution environment
# All commands run here, never on the host
# ============================================================
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget jq tree ripgrep vim-common nodejs npm file poppler-utils poppler-data \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 -s /bin/bash sandbox
RUN mkdir -p /workspace && chown sandbox:sandbox /workspace && chmod 700 /workspace

RUN pip install --no-cache-dir --break-system-packages \
    requests httpx beautifulsoup4 pandas numpy duckdb

WORKDIR /workspace
USER sandbox
CMD ["sleep", "infinity"]
