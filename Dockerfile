# --- Support Desk (AutoGen + Streamlit) -----------------------------------
FROM python:3.11-slim

# Keep Python output unbuffered so logs show up immediately in `docker logs`
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    TICKETS_FILE=/app/data/tickets.txt

WORKDIR /app

# System deps needed for the healthcheck (curl)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first so Docker can cache this layer between builds
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the actual app code
COPY app.py .

# tickets.txt lives here; mount this as a volume to persist ticket history
RUN mkdir -p /app/data

# Run as a non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl --fail http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py"]
