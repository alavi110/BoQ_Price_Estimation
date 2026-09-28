FROM python:3.11-slim

WORKDIR /app

# Install system dependencies.
# libpango/libcairo/libharfbuzz back the WeasyPrint PDF pipeline; harfbuzz is
# what actually shapes the Arabic script in the defense document, so it is
# required rather than optional.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    g++ \
    cmake \
    pkg-config \
    libffi-dev \
    libssl-dev \
    libxml2-dev \
    libxslt-dev \
    libjpeg-dev \
    libpng-dev \
    libcairo2-dev \
    libpango1.0-dev \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libharfbuzz-subset0 \
    libgdk-pixbuf-xlib-2.0-dev \
    shared-mime-info \
    fonts-dejavu-core \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Vazirmatn is the Persian typeface the defense document ships with. It has to
# be installed system-wide so WeasyPrint can resolve it, and into the app tree
# so the HTML export can reference it via a file:// URL.
ARG VAZIRMATN_VERSION=3.100
ENV VAZIRMATN_DIR=/usr/share/fonts/truetype/vazirmatn
RUN mkdir -p "${VAZIRMATN_DIR}" \
    && curl -fsSL -o /tmp/vazirmatn.zip \
       "https://github.com/rastikerdar/vazirmatn/releases/download/v${VAZIRMATN_VERSION}/vazirmatn-v${VAZIRMATN_VERSION}.zip" \
    && python -c "import zipfile; zipfile.ZipFile('/tmp/vazirmatn.zip').extractall('/tmp/vazirmatn')" \
    && find /tmp/vazirmatn -name 'Vazirmatn-*.woff2' -exec cp {} "${VAZIRMATN_DIR}/" \; \
    && cp /tmp/vazirmatn/fonts/ttf/Vazirmatn-Regular.ttf "${VAZIRMATN_DIR}/" 2>/dev/null || true \
    && fc-cache -f >/dev/null 2>&1 || true \
    && rm -rf /tmp/vazirmatn /tmp/vazirmatn.zip

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app \
    && mkdir -p /app/exports \
    && chown app:app /app/exports
USER app

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]