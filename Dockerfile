# Use the official uv image for building
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

# Set the working directory
WORKDIR /app

# Enable bytecode compilation and use copy mode for linking
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install dependencies first to leverage Docker caching
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy the rest of the application source code
COPY . /app

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Use a slim Python image for the final stage
FROM python:3.13-slim

# Set the working directory
WORKDIR /app

# Copy the virtual environment and the application from the builder
COPY --from=builder /app/.venv /app/.venv
COPY main.py /app/

# Set the path to use the virtual environment
ENV PATH="/app/.venv/bin:$PATH"

# Expose the port the app runs on
EXPOSE 8000

# Run the FastAPI application
CMD ["fastapi", "run", "main.py", "--port", "8000", "--host", "0.0.0.0"]
