#!/usr/bin/env bash
set -euo pipefail

# Safely recover the legacy docker-compose v1 ContainerConfig recreate failure.
# Only the web service container is removed; named volumes and Redis are preserved.
if docker info >/dev/null 2>&1; then DOCKER=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then DOCKER=(sudo docker)
else echo 'Docker daemon is unavailable. Check sudo systemctl status docker.' >&2; exit 1; fi

if "${DOCKER[@]}" compose version >/dev/null 2>&1; then
  COMPOSE=("${DOCKER[@]}" compose)
else
  command -v docker-compose >/dev/null 2>&1 || { echo 'Install Docker Compose V2 (recommended) or docker-compose.' >&2; exit 1; }
  if [[ "${DOCKER[0]}" == sudo ]]; then COMPOSE=(sudo docker-compose); else COMPOSE=(docker-compose); fi
fi

"${COMPOSE[@]}" rm -sf web
"${COMPOSE[@]}" up -d --build web
"${COMPOSE[@]}" ps
