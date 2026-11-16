FROM docker.io/library/debian:bookworm
RUN apt-get update && apt-get install -y --no-install-recommends systemd systemd-sysv bubblewrap curl ca-certificates procps && rm -rf /var/lib/apt/lists/*
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
