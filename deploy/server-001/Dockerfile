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

# ---- python deps (hash-verified) ---------------------------------------------
# Every registry package below is verified against a SHA-256 in ../../requirements.txt,
# generated from uv.lock (uv export writes the hashes; there is no --require-hashes flag
# on `uv sync` or `uv export` — enforcement lives on `pip install`).
#
# Regenerate after any lock change, from the repo root:
#   uv export --no-dev --extra space --extra durable --no-emit-project -o requirements.txt
#   # then move the bare `jev-compact @ git+...` line into deploy/server-001/requirements-vcs.txt
# `tests/unit/test_provisioning_pins.py` fails when the committed file drifts from the lock.
FROM python:3.12-slim-bookworm AS py
# git: jev-compact is a pinned git dependency, installed separately below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && apt-get clean
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1
WORKDIR /app
# `uv.lock` is deliberately absent: this stage installs from the exported, hash-pinned file, not from the lock.
COPY pyproject.toml requirements.txt ./
COPY deploy/server-001/requirements-vcs.txt ./
# Two steps, because pip cannot hash a version-control source at all: it refuses with
# "Can't verify hashes for these requirements because we don't have a way to hash
# version control repositories". The registry packages in requirements.txt are verified
# by hash; the VCS dependency's 40-character commit pin is its integrity guarantee.
# `space` = e2b SDK for the SPACE backend; `durable` = temporalio, which used to be
# installed outside the lock entirely.
RUN uv venv --python 3.12 /opt/venv \
    && uv pip install --python /opt/venv/bin/python --no-cache --require-hashes -r requirements.txt \
    && uv pip install --python /opt/venv/bin/python --no-cache --no-deps -r requirements-vcs.txt

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
