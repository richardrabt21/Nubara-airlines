# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# ---- test image: adds dev deps and the test suite
FROM base AS test
COPY tests ./tests
COPY scripts ./scripts
RUN pip install --no-cache-dir ".[dev]"
CMD ["pytest", "-q"]

# ---- runtime image: non-root, minimal
FROM base AS runtime
RUN useradd --create-home appuser
USER appuser
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "nubara_companion.api:app", "--host", "0.0.0.0", "--port", "8000"]
