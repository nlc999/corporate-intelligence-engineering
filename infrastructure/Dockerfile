# Isolated multi-stage Python build for the synthetic OSINT showcase.
FROM python:3.11-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels ".[postgres]"

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels osint-research && rm -rf /wheels && useradd --system --uid 10001 --create-home runner
COPY scripts ./scripts
COPY examples ./examples
COPY sql ./sql
USER runner
ENTRYPOINT ["python", "scripts/search_demo.py"]
CMD ["search", "GREENLEAF ENGINEERING LIMITED"]
