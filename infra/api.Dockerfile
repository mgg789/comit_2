FROM node:22-bookworm AS build
WORKDIR /src
RUN corepack enable && corepack prepare pnpm@9.14.2 --activate
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml tsconfig.base.json ./
COPY packages ./packages
COPY apps/api ./apps/api
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @comit/api build
# deploy prunes to production deps for this workspace package only
RUN pnpm --filter @comit/api deploy --prod /out

FROM node:22-bookworm-slim
WORKDIR /app
COPY --from=build /out /app
ENV PORT=8080
EXPOSE 8080
CMD ["node", "dist/server.js"]
