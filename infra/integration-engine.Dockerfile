FROM python:3.12-slim
WORKDIR /app
COPY services/integration-engine/pyproject.toml ./pyproject.toml
COPY services/integration-engine/src ./src
RUN pip install --no-cache-dir .
ENV PORT=8103
EXPOSE 8103
CMD ["python", "-m", "integration_engine"]
