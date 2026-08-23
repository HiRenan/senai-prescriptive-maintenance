FROM node:22-alpine3.22@sha256:cd7807368cf24826297cbad5dca1a44972ccfd770647db52a8c7589eb4599ac8 AS builder

WORKDIR /workspace

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json ./apps/web/package.json

RUN corepack pnpm install --frozen-lockfile --prod \
    --filter @senai-prescriptive-maintenance/web

COPY apps/web/server.mjs ./apps/web/server.mjs

FROM node:22-alpine3.22@sha256:cd7807368cf24826297cbad5dca1a44972ccfd770647db52a8c7589eb4599ac8 AS runtime

ENV HOST=0.0.0.0 \
    NODE_ENV=production \
    PORT=3000

WORKDIR /app

COPY --from=builder --chown=node:node /workspace/apps/web/package.json ./package.json
COPY --from=builder --chown=node:node /workspace/apps/web/server.mjs ./server.mjs

USER node

EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["node", "--input-type=module", "--eval", "const response = await fetch('http://127.0.0.1:3000/health/live'); const body = await response.text(); process.exit(response.status === 200 && response.headers.get('content-type') === 'application/json' && body === '{\"status\":\"ok\"}' ? 0 : 1);"]

CMD ["node", "server.mjs"]
