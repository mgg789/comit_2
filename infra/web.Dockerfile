# Builds the three product web apps and serves them from a single nginx:
#   /            -> apps/welcome    /candidate/ -> apps/candidate
#   /employer/   -> apps/employer (testing remains a backend only)
FROM node:22-bookworm AS build
WORKDIR /src
RUN corepack enable && corepack prepare pnpm@9.14.2 --activate
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml tsconfig.base.json ./
COPY packages ./packages
COPY apps ./apps
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @comit/shared --filter @comit/ui-kit --filter @comit/api-client --filter @comit/web-welcome --filter @comit/web-candidate --filter @comit/web-employer build

FROM nginx:1.27-alpine
COPY infra/nginx/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/apps/welcome/dist /usr/share/nginx/html/welcome/
COPY --from=build /src/apps/candidate/dist /usr/share/nginx/html/candidate/
COPY --from=build /src/apps/employer/dist /usr/share/nginx/html/employer/
# Immutable releases are extracted under umask 077; public copied assets must be
# readable by nginx workers, not only the root build user.
RUN chmod -R a+rX /usr/share/nginx/html
EXPOSE 80
