# ============================================================
# Sandbox image — isolated execution environment
# All commands run here, never on the host
# ============================================================
FROM python:3.11-slim

ARG INSTALL_OFFICE_RENDERER=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget jq tree ripgrep vim-common nodejs npm file poppler-utils poppler-data \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
    && if [ "$INSTALL_OFFICE_RENDERER" = "true" ]; then \
         apt-get install -y --no-install-recommends \
           libreoffice-writer libreoffice-calc libreoffice-impress fonts-noto-cjk; \
       fi \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 -s /bin/bash sandbox
RUN mkdir -p /workspace && chown sandbox:sandbox /workspace && chmod 700 /workspace

RUN pip install --no-cache-dir --break-system-packages \
    requests httpx beautifulsoup4 pandas numpy duckdb \
    pdfplumber==0.11.9 pypdfium2==5.8.0 python-docx==1.2.0 \
    openpyxl==3.1.5 python-pptx==1.0.2 Pillow==12.2.0

WORKDIR /workspace
USER sandbox
CMD ["sleep", "infinity"]
