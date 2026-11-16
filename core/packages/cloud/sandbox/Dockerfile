# Task sandbox image. Cloudflare Containers run linux/amd64 only and wrangler builds for that platform.
# The image contains no keys and no credentials: anything a sandbox process needs to reach is added outside
# the container by the outbound handler.
# `cloudflare/sandbox` is not an operating system: it holds only the `sandbox-shim` helper that the SDK's Files
# class runs, so the shim is copied onto a small Debian base. Keep the shim tag equal to the @cloudflare/sandbox
# version in package.json.
FROM docker.io/cloudflare/sandbox:1.0.3 AS shim

FROM docker.io/library/debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
COPY --from=shim /usr/local/bin/sandbox-shim /usr/local/bin/sandbox-shim
WORKDIR /workspace
