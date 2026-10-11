# Build context: repository root (the web app depends on the packages/graph-contract workspace).
# syntax=docker/dockerfile:1

# ---------- build ------------------------------------------------------------------------------
FROM node:20-bookworm-slim AS build
WORKDIR /repo

COPY package.json package-lock.json ./
COPY packages/graph-contract/package.json packages/graph-contract/
COPY apps/web/package.json apps/web/
COPY services/api/package.json services/api/
RUN npm ci --ignore-scripts

COPY packages/graph-contract packages/graph-contract
COPY apps/web apps/web
RUN npm run build --workspace @lng/web

# ---------- runtime ----------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/web/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=5 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
