# Build context: repository root (the API depends on the packages/graph-contract workspace).
# syntax=docker/dockerfile:1

# ---------- build ------------------------------------------------------------------------------
FROM node:20-bookworm-slim AS build
WORKDIR /repo

COPY package.json package-lock.json ./
COPY packages/graph-contract/package.json packages/graph-contract/
COPY services/api/package.json services/api/
COPY apps/web/package.json apps/web/
RUN npm ci --ignore-scripts

COPY packages/graph-contract packages/graph-contract
COPY services/api services/api
RUN npm run build --workspace @lng/api

# ---------- runtime ----------------------------------------------------------------------------
FROM node:20-bookworm-slim AS runtime

ENV NODE_ENV=production \
    API_PORT=4000 \
    LOG_LEVEL=info

WORKDIR /app

# Install only the API's runtime dependencies. The workspace dependency (@lng/graph-contract) is
# bundled into dist/index.js by esbuild, so it is dropped from the manifest before installing.
COPY services/api/package.json ./services/api/package.json
RUN node -e "const fs=require('fs');const p=JSON.parse(fs.readFileSync('/app/services/api/package.json','utf8'));delete p.dependencies['@lng/graph-contract'];delete p.devDependencies;fs.writeFileSync('/app/services/api/package.json',JSON.stringify(p,null,2))" \
 && cd services/api && npm install --omit=dev --no-audit --no-fund

COPY --from=build /repo/services/api/dist ./services/api/dist

RUN useradd --create-home --uid 10002 api && chown -R api:api /app/services/api
USER api
WORKDIR /app/services/api

EXPOSE 4000

# node:20 ships fetch, so no extra tooling is needed for the probe (compose also uses /health).
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=6 \
  CMD node -e "fetch('http://127.0.0.1:4000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "dist/index.js"]
