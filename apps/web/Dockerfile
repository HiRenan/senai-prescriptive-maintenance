FROM scratch AS context-audit

COPY . /context

FROM node:22-alpine3.22@sha256:cd7807368cf24826297cbad5dca1a44972ccfd770647db52a8c7589eb4599ac8 AS deps

WORKDIR /workspace

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json ./apps/web/package.json

# The bundler and its plugins are development dependencies, so the build stage
# needs the full install; nothing from node_modules reaches the runtime image.
RUN corepack pnpm install --frozen-lockfile \
    --filter @senai-prescriptive-maintenance/web

FROM deps AS builder

COPY apps/web/index.html ./apps/web/index.html
COPY apps/web/vite.config.ts ./apps/web/vite.config.ts
COPY apps/web/public ./apps/web/public
COPY apps/web/src ./apps/web/src
COPY apps/web/server.mjs ./apps/web/server.mjs

RUN corepack pnpm --filter @senai-prescriptive-maintenance/web build

FROM builder AS builder-audit

RUN set -eu; \
    test ! -e /workspace/apps/api; \
    test ! -e /workspace/apps/web/README.md; \
    test ! -e /workspace/apps/web/tests; \
    test ! -e /workspace/apps/web/playwright.config.ts; \
    test ! -e /workspace/apps/web/tsconfig.json; \
    test -z "$(find /workspace/apps/web -name node_modules -prune -o -type f ! -name 'package.json' ! -name 'server.mjs' ! -name 'vite.config.ts' ! -name '*.css' ! -name '*.html' ! -name '*.js' ! -name '*.svg' ! -name '*.ts' ! -name '*.tsx' -print -quit)"; \
    test -z "$(find /workspace/apps/web/src /workspace/apps/web/public -type f -name '*.d.ts' -print -quit)"; \
    test -z "$(find /workspace -name node_modules -prune -o -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.pdf' -o -name '*.csv' -o -name '.env' -o -name '.env.*' \) -print -quit)"; \
    test -z "$(find /workspace -name node_modules -prune -o -type d \( -name '__pycache__' -o -name '.git' -o -name '.cache' -o -name '.npm' -o -name '.pnpm-store' \) -print -quit)"; \
    printf '%s\n' 'Web builder audit: repository residues and caches absent.'

# The runtime serves the build, so the bundle must match the same closed
# grammar the AWS publication enforces, and server.mjs must find the generated
# contract it imports at boot.
RUN set -eu; \
    test -f /workspace/apps/web/dist/index.html; \
    test -f /workspace/apps/web/dist/favicon.svg; \
    test -f /workspace/apps/web/dist/theme-init.js; \
    test -d /workspace/apps/web/dist/assets; \
    test -z "$(find /workspace/apps/web/dist -mindepth 2 -type d -print -quit)"; \
    test -z "$(find /workspace/apps/web/dist -type f ! -name '*.css' ! -name '*.html' ! -name '*.js' ! -name '*.svg' -print -quit)"; \
    test -f /workspace/apps/web/src/generated/analysis-contract.js; \
    test -f /workspace/apps/web/src/generated/document-contract.js; \
    printf '%s\n' 'Web builder audit: production bundle matches the published grammar.'

FROM node:22-alpine3.22@sha256:cd7807368cf24826297cbad5dca1a44972ccfd770647db52a8c7589eb4599ac8 AS runtime

ENV HOST=0.0.0.0 \
    NODE_ENV=production \
    PORT=3000

WORKDIR /app

# React ships inside the bundle and server.mjs only uses Node builtins, so the
# runtime image carries no node_modules at all.
COPY --from=builder --chown=node:node /workspace/apps/web/package.json ./package.json
COPY --from=builder --chown=node:node /workspace/apps/web/server.mjs ./server.mjs
COPY --from=builder --chown=node:node /workspace/apps/web/dist ./dist
COPY --from=builder --chown=node:node /workspace/apps/web/src/generated ./src/generated

USER node

EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["node", "--input-type=module", "--eval", "const response = await fetch('http://127.0.0.1:3000/health/live'); const body = await response.text(); process.exit(response.status === 200 && response.headers.get('content-type') === 'application/json' && body === '{\"status\":\"ok\"}' ? 0 : 1);"]

CMD ["node", "server.mjs"]
