FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so edits to source do not invalidate the layer.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[postgres]"

# Non-root. A scraper parses untrusted input from the open internet, which is
# not something to run with more privilege than it needs.
RUN useradd --create-home --uid 1000 scraper \
    && mkdir -p /app/snapshots \
    && chown -R scraper:scraper /app
USER scraper

EXPOSE 9100

HEALTHCHECK --interval=5m --timeout=10s --start-period=30s \
    CMD scraper health || exit 1

ENTRYPOINT ["scraper"]
CMD ["run", "books"]
