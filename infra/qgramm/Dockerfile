# QGramm is built from a pinned source snapshot so the bridge cannot silently
# drift with a moving upstream branch.
FROM golang:1.26-alpine@sha256:8ac98ca534ac3f51e1f420a1dd2c15e74c75cfa0f23f3ad27eb5d7236c349a0c AS build
ARG QGRAMM_REPOSITORY=https://github.com/mgg789/QGramm.git
ARG QGRAMM_COMMIT=f22de87f369cad0b34d243c97ad876a90eb9d6db
WORKDIR /src
RUN apk add --no-cache git ca-certificates \
 && git clone "$QGRAMM_REPOSITORY" qgramm \
 && cd qgramm \
 && git checkout --detach "$QGRAMM_COMMIT"
COPY infra/qgramm/bridge /src/qgramm/bridge
COPY infra/qgramm/comit.toml /src/qgramm/deploy/comit.toml
WORKDIR /src/qgramm
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
 CGO_ENABLED=0 go test -mod=readonly -tags qg_files ./bridge \
 && CGO_ENABLED=0 go build -mod=readonly -tags qg_files -trimpath -ldflags='-s -w' -o /out/comit-chat ./bridge

FROM alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce
RUN addgroup -S -g 10001 comit && adduser -S -D -H -u 10001 -G comit comit \
 && mkdir -p /data/files && chown -R comit:comit /data
COPY --from=build /out/comit-chat /comit-chat
COPY --from=build /src/qgramm/LICENSE /src/qgramm/NOTICE /src/qgramm/THIRD_PARTY_LICENSES.txt /licenses/
COPY infra/qgramm/comit.toml /etc/comit/qgramm.toml
RUN chown comit:comit /comit-chat /etc/comit/qgramm.toml
USER comit
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD wget -q -O - http://127.0.0.1:8080/readyz || exit 1
ENTRYPOINT ["/comit-chat"]
