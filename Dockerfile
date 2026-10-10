FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY stridon ./stridon
RUN pip install --no-cache-dir .

WORKDIR /work
ENTRYPOINT ["stridon"]
