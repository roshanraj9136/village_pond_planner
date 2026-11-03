# syntax=docker/dockerfile:1
#
# JalDrishti gateway (Go) serving the built React frontend, instrumented with OpenTelemetry.
# Built from the repository root by docker-compose.yml (context ".").

# ---- 1. frontend (Vite needs Node 20.19+ / 22.12+)
FROM node:22.23.4-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# pre-compressed copies, like deploy/deploy.sh makes for the lab
RUN npm run build \
    && find dist -type f \( -name '*.js' -o -name '*.css' -o -name '*.svg' -o -name '*.html' \) -exec gzip -k -9 {} \;

# ---- 2. gateway binary
FROM golang:1.27-alpine AS gateway
WORKDIR /src
COPY gateway/go.mod gateway/go.sum ./
RUN go mod download
COPY gateway/*.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/gateway .

# ---- 3. runtime: only the binary and the static files
FROM gcr.io/distroless/static-debian13:nonroot
COPY --from=gateway /out/gateway /gateway
COPY --from=frontend /src/dist /app/dist
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/gateway"]
