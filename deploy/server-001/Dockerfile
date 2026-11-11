# open-computer on server-001: ONE image, three roles (console, Temporal worker,
# device relay) selected by the compose `command`. Build context = repo root:
#   docker build -f deploy/server-001/Dockerfile -t oc-server-001:<sha> .

# ---- console UI (React/Vite) -------------------------------------------------
FROM node:24-bookworm-slim AS ui
WORKDIR /ui
RUN corepack enable
COPY ui/package.json ui/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY ui/ ./
RUN pnpm build

# ---- python deps (locked) ----------------------------------------------------
FROM python:3.12-slim-bookworm AS py
# git: jev-compact is a pinned git dependency.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && apt-get clean
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# `space` extra = e2b SDK for the SPACE backend. temporalio is not in the lock file
# (the laptop launchers add it with `uv run --with`); pinned to the version they run.
RUN uv sync --locked --no-dev --no-install-project --extra space \
    && uv pip install --python /opt/venv/bin/python "temporalio==1.35.0"

# ---- runtime -----------------------------------------------------------------
FROM python:3.12-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates curl \
    && apt-get clean \
    && groupadd -r oc -g 1000 && useradd -r -u 1000 -g oc -d /home/oc -m oc \
    && mkdir -p /home/oc/.local/share/oc && chown -R oc:oc /home/oc
COPY --from=py /opt/venv /opt/venv
WORKDIR /app
COPY pyproject.toml README.md ./
COPY oc ./oc
COPY services ./services
COPY --from=ui /ui/dist ./ui/dist
ARG OC_COMMIT=unknown
LABEL org.opencontainers.image.revision=$OC_COMMIT
ENV PATH=/opt/venv/bin:$PATH \
    HOME=/home/oc \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OC_COMMIT=$OC_COMMIT \
    OC_ROOT=/home/oc/.local/share/oc
USER oc
EXPOSE 8791
# Default role: the console. Compose overrides the command for worker / relay.
CMD ["python", "-m", "services.opsview.server", "8791"]
