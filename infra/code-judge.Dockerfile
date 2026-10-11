FROM docker:29-cli AS docker_cli
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
COPY services/code-judge/pyproject.toml ./
COPY services/code-judge/src ./src
RUN pip install --no-cache-dir . && useradd --uid 10001 judge && mkdir -p /data/judge && chown judge /data/judge
USER judge
ENV CODE_JUDGE_PORT=8106 CODE_JUDGE_DATA_DIR=/data/judge PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "code_judge"]
