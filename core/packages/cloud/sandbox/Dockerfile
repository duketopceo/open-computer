# Task sandbox image. Cloudflare Containers run linux/amd64 only and wrangler builds for that platform.
# The image contains no keys and no credentials: anything a sandbox process needs to reach is added outside
# the container by the outbound handler. `cloudflare/sandbox` ships the `sandbox-shim` helper that the SDK's
# Files class runs; keep this tag equal to the @cloudflare/sandbox version in package.json.
FROM docker.io/cloudflare/sandbox:1.0.3

WORKDIR /workspace
