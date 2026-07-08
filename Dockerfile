FROM python:3.11-slim

RUN useradd --create-home --uid 1000 forticnapp
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY lw.yaml ./lw.yaml
RUN pip install --no-cache-dir .

USER forticnapp

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1

CMD ["forticnapp-mcp-http"]
