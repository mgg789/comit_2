FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/verification-engine/pyproject.toml ./pyproject.toml
COPY services/verification-engine/src ./src
RUN pip install --no-cache-dir .
ENV PORT=8105
EXPOSE 8105
CMD ["python", "-m", "verification_engine"]
