# ============================================================
# Sandbox image — isolated execution environment
# All commands run here, never on the host
# ============================================================
# 2026-10-21 security release; do not use Debian's older nodejs/npm packages.
FROM node:24.22.1-bookworm-slim AS node-runtime

FROM python:3.13-slim

ARG INSTALL_OFFICE_RENDERER=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget jq tree ripgrep vim-common ca-certificates libstdc++6 libatomic1 file poppler-utils poppler-data \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
    && if [ "$INSTALL_OFFICE_RENDERER" = "true" ]; then \
         apt-get install -y --no-install-recommends \
           libreoffice-writer libreoffice-calc libreoffice-impress fonts-noto-cjk; \
       fi \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN useradd -m -u 1000 -s /bin/bash sandbox
RUN mkdir -p /workspace && chown sandbox:sandbox /workspace && chmod 700 /workspace

RUN pip install --no-cache-dir --break-system-packages \
    requests httpx beautifulsoup4 pandas numpy duckdb \
    pdfplumber==0.11.9 pypdfium2==5.8.0 python-docx==1.2.0 \
    openpyxl==3.1.5 python-pptx==1.0.2 Pillow==12.2.0

WORKDIR /workspace
USER sandbox
# Validate the copied runtime and npm entrypoints as the actual sandbox user
# on every target architecture, including CI's amd64 and arm64 builds.
RUN test "$(node --version)" = "v24.22.1" && npm --version && npx --version
CMD ["sleep", "infinity"]
