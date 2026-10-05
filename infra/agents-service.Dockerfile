FROM python:3.12-slim
WORKDIR /app
COPY services/agents-service/pyproject.toml ./pyproject.toml
COPY services/agents-service/src ./src
RUN pip install --no-cache-dir .
ENV PORT=8104
EXPOSE 8104
CMD ["python", "-m", "agents_service"]
