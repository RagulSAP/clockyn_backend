FROM python:3.11-slim

# Prevent Python from writing pyc files & enable logs immediately
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install ALL required system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    g++ \
    cmake \
    git \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    fonts-dejavu-core \
    pkg-config \
    libcairo2-dev \
    libpango1.0-dev \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first (for caching)
COPY requirements.txt .

# Upgrade pip and install Python dependencies (still as root: packages go to the system site-packages)
RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

# --- Non-root runtime user -------------------------------------------------------------------
# The app writes only to /tmp and S3, plus the font/cairo caches of the PDF libraries, which live
# under $HOME. So the user gets a real, writable home. (Face recognition runs on AWS Rekognition:
# no model is downloaded or loaded locally; ml/models/modelrgb.onnx ships inside the image.)
#   Fixed uid/gid 10001 keeps volume ownership predictable across rebuilds; --create-home makes
#   /home/app; --no-log-init avoids a huge sparse /var/log/lastlog for large uids; the nologin
#   shell means nobody can open an interactive session as this user.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin --no-log-init app \
    && mkdir -p /home/app/.cache \
    && chown -R app:app /home/app

# HOME is set explicitly so caches resolve to /home/app for the app user (and for any
# `docker exec -u app` session); XDG_CACHE_HOME keeps font caches in the same place.
ENV HOME=/home/app
ENV XDG_CACHE_HOME=/home/app/.cache

# Copy full project, owned by the app user (--chown avoids a second layer that a separate
# `chown -R /app` would duplicate; ml/models/modelrgb.onnx is read via the relative path
# "ml/models/modelrgb.onnx", so WORKDIR /app must stay the working directory).
COPY --chown=app:app . .

# Everything from here runs unprivileged
USER app

# Expose port (FastAPI)
EXPOSE 8080

# Liveness probe without curl: python is always present. /health is public and never touches the
# database. start-period stays generous: migrations (RUN_MIGRATIONS=1) and the heavy imports run
# before uvicorn begins listening.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"]

# Run app
# --proxy-headers / --forwarded-allow-ips: the container only receives traffic from the load balancer (security group), so trust
# its X-Forwarded-Proto / -For headers: the app then sees https and the real client address (signed links, redirects).
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
