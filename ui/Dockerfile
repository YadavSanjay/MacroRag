# MacroRAG UI image — static SPA on nginx that reverse-proxies /api -> the API.
#
# Build from the REPO ROOT (so the build stage can copy ui/ and the root
# .dockerignore applies):
#   docker build -f ui/Dockerfile -t macrorag-ui:latest .
#
# Run (point at a reachable API; default assumes the compose "app" service):
#   docker run -p 8080:8080 -e API_UPSTREAM=host.docker.internal:8000 macrorag-ui:latest
#
# The bundle talks to a RELATIVE /api (see ui/src/api/client.ts), so the same
# image runs in every environment — only API_UPSTREAM (the proxy target) varies
# at run time. No backend URL is baked into the JavaScript.

# ---- build stage --------------------------------------------------------------
# Pin the node minor to match the dev toolchain (ui/CLAUDE.md); bump deliberately.
FROM node:24.14.0-alpine AS build
WORKDIR /app/ui

# Deps first for a cacheable layer; npm ci is deterministic from the lockfile.
COPY ui/package.json ui/package-lock.json ./
RUN npm ci

# Then sources, then build (tsc && vite build -> /app/ui/dist).
COPY ui/ ./
RUN npm run build

# ---- runtime stage ------------------------------------------------------------
# Unprivileged nginx: runs as uid 101 and listens on 8080 out of the box
# (no root, no cap_net_bind gymnastics).
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

# Where nginx forwards /api/*. Overridable at run time (compose/helm set this).
ENV API_UPSTREAM=app:8000

# Static assets.
COPY --from=build /app/ui/dist /usr/share/nginx/html

# nginx's entrypoint runs envsubst over /etc/nginx/templates/*.template at start,
# writing the result to /etc/nginx/conf.d/ — that's how ${API_UPSTREAM} is
# substituted at run time rather than baked in.
COPY ui/nginx/default.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 8080
