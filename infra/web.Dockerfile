# Builds all four web apps once and serves them from a single nginx:
#   /            -> apps/welcome    /candidate/ -> apps/candidate
#   /employer/   -> apps/employer   /testing/   -> apps/testing
FROM node:22-bookworm AS build
WORKDIR /src
RUN corepack enable && corepack prepare pnpm@9.14.2 --activate
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml tsconfig.base.json ./
COPY packages ./packages
COPY apps ./apps
RUN pnpm install --frozen-lockfile
RUN pnpm -r build

FROM nginx:1.27-alpine
COPY infra/nginx/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/apps/welcome/dist /usr/share/nginx/html/welcome/
COPY --from=build /src/apps/candidate/dist /usr/share/nginx/html/candidate/
COPY --from=build /src/apps/employer/dist /usr/share/nginx/html/employer/
COPY --from=build /src/apps/testing/dist /usr/share/nginx/html/testing/
EXPOSE 80
