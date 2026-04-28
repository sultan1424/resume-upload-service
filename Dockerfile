# ─────────────────────────────────────────
# Stage 1: Base image
# Factor 2: Dependencies — all deps bundled inside the image
# Factor 10: Dev/Prod parity — same image runs everywhere
# ─────────────────────────────────────────
FROM python:3.11-slim

# Set working directory inside the container
WORKDIR /app

# Copy dependencies file first (Docker cache optimization)
# If requirements.txt didn't change, this layer is cached
COPY requirements.txt .

# Install all dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .

# ─────────────────────────────────────────
# Factor 7: Port Binding — expose the port
# ─────────────────────────────────────────
EXPOSE 8080

# ─────────────────────────────────────────
# Factor 9: Disposability — gunicorn handles
# graceful shutdown on SIGTERM automatically
# ─────────────────────────────────────────
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "60", "app:app"]
