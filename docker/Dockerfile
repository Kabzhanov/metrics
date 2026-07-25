FROM python:3.11-slim
WORKDIR /app
COPY metrics_mcp/ ./metrics_mcp/
COPY pyproject.toml ./
COPY schema/ ./schema/
COPY detect_degradation.py ./
COPY benchmark_runner.py ./
COPY docs/ ./docs/
RUN pip install --no-cache-dir -e .
RUN pip install psycopg2-binary
EXPOSE 8000
CMD ["python", "-m", "metrics_mcp.server"]
