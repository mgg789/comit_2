FROM python:3.12-slim
WORKDIR /app
COPY services/matching-engine/pyproject.toml ./pyproject.toml
COPY services/matching-engine/src ./src
# Harrier runs on CPU; keep CUDA runtime wheels out of the production image.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu 'torch==2.14.1' \
 && pip install --no-cache-dir '.[embeddings]'
ENV PORT=8101
EXPOSE 8101
CMD ["python", "-m", "matching_engine"]
