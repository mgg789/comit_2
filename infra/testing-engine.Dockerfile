FROM python:3.12-slim
WORKDIR /app
COPY services/testing-engine/pyproject.toml ./pyproject.toml
COPY services/testing-engine/src ./src
COPY services/testing-engine/blueprints ./blueprints
RUN pip install --no-cache-dir .
ENV PORT=8102
EXPOSE 8102
CMD ["python", "-m", "testing_engine"]
