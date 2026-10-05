FROM python:3.12-slim
WORKDIR /app
COPY services/matching-engine/pyproject.toml ./pyproject.toml
COPY services/matching-engine/src ./src
RUN pip install --no-cache-dir '.[embeddings]'
ENV PORT=8101
EXPOSE 8101
CMD ["python", "-m", "matching_engine"]
