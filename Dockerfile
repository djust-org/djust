# Multi-stage build for djust
# Stage 1: Build Rust components
FROM rust:1.75-slim-bookworm AS rust-builder

WORKDIR /build

# Install build dependencies (Python 3.11 to match runtime)
RUN apt-get update && apt-get install -y \
    python3.11-dev \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Install maturin for building Python extensions
RUN python3.11 -m pip install --break-system-packages maturin

# Copy Rust workspace
COPY Cargo.toml README.md ./
COPY crates/ ./crates/
COPY python/ ./python/
COPY pyproject.toml ./

# Build the Rust extension for AMD64 with Python 3.11 (let cargo generate Cargo.lock)
RUN python3.11 -m maturin build --release --target x86_64-unknown-linux-gnu --interpreter python3.11

# Stage 2: Runtime image
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first
RUN pip install --no-cache-dir \
    Django>=3.2 \
    channels[daphne]>=4.0.0 \
    msgpack>=1.0.0 \
    uvicorn[standard]>=0.30.0 \
    whitenoise>=6.0.0 \
    gunicorn>=21.0.0

# Copy and install built wheel from builder
COPY --from=rust-builder /build/target/wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -rf /tmp/*.whl

# Copy demo project
COPY examples/demo_project/ ./demo_project/

# Set Python path to include our package
ENV PYTHONPATH=/app/python:/app:$PYTHONPATH

# Create necessary directories
RUN mkdir -p /app/demo_project/staticfiles /app/demo_project/media

# Collect static files
WORKDIR /app/demo_project
RUN python manage.py collectstatic --noinput || true

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Run with Uvicorn (supports WebSockets via ASGI)
CMD ["uvicorn", "demo_project.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
