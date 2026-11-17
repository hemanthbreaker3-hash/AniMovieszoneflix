#!/usr/bin/env bash
# Managed by deploy.vps. Starts a Cloudflare Quick Tunnel and automatically synchronizes its
# generated HTTPS trycloudflare.com URL into .env and MongoDB for download, stream, and button URLs.
set -Eeuo pipefail

SCRIPT_DIR="${FILE2LINK_DIR:-$(pwd)}"
if [[ ! -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
fi
cd "$SCRIPT_DIR"

# Load .env so TRYURL / PORT from the project file are available
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env" 2>/dev/null || true
  set +a
fi

PORT_VALUE="${PORT:-8000}"
# Public VPS IP/host for cloudflared origin — from credentials, same as other secrets:
#   export TRYURL=138.68.175.206
#   or in .env / config.env:  TRYURL=138.68.175.206
# shell export wins, then .env (TRYURL / tryurl), then default
if [[ -z "${TRYURL:-}" ]]; then
  TRYURL="$(python3 - <<'PY'
from pathlib import Path
for path in (Path(".env"), Path("config.env")):
    if not path.exists():
        continue
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if k.strip() in ("TRYURL", "tryurl", "TRY_URL", "TUNNEL_ORIGIN_HOST"):
            v = v.strip().strip('"').strip("'")
            if v:
                print(v)
                raise SystemExit
print("138.68.175.206")
PY
)"
fi
export TRYURL="${TRYURL:-138.68.175.206}"
ORIGIN_HOST="${TRYURL}"
ORIGIN_URL="http://${ORIGIN_HOST}:${PORT_VALUE}"

LOG_FILE="$SCRIPT_DIR/cloudflared-quick-tunnel.log"
COMPOSE=(docker compose --project-directory "$SCRIPT_DIR" -f "$SCRIPT_DIR/docker-compose.yml" --env-file "$SCRIPT_DIR/.env")
if ! docker info >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
  COMPOSE=(sudo docker compose --project-directory "$SCRIPT_DIR" -f "$SCRIPT_DIR/docker-compose.yml" --env-file "$SCRIPT_DIR/.env")
fi

log() { printf '[File2link Cloudflare] %s\n' "$*"; }

if [[ ! -x /usr/local/bin/cloudflared ]]; then
  log 'cloudflared binary is missing; the deployment installer must install it first.'
  exit 1
fi

# Health: accept either local loopback or the public TRYURL origin
health_ok() {
  curl -fsS "http://127.0.0.1:${PORT_VALUE}/health" >/dev/null 2>&1 \
    || curl -fsS "${ORIGIN_URL}/health" >/dev/null 2>&1
}

for _ in $(seq 1 90); do
  if health_ok; then
    break
  fi
  sleep 2
done
if ! health_ok; then
  log "File2link health endpoint is not ready on 127.0.0.1:${PORT_VALUE} or ${ORIGIN_URL}; refusing to start the public tunnel."
  exit 1
fi

: > "$LOG_FILE"
log "TRYURL=${TRYURL}  PORT=${PORT_VALUE}"
log "Starting Quick Tunnel origin → ${ORIGIN_URL}"
/usr/local/bin/cloudflared tunnel --no-autoupdate --protocol http2 --loglevel info --url "${ORIGIN_URL}" >"$LOG_FILE" 2>&1 &
CF_PID=$!
cleanup() {
  if kill -0 "$CF_PID" 2>/dev/null; then kill "$CF_PID" 2>/dev/null || true; fi
  wait "$CF_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

TUNNEL_URL=''
for _ in $(seq 1 90); do
  if ! kill -0 "$CF_PID" 2>/dev/null; then
    log 'cloudflared exited before creating a tunnel.'
    tail -n 80 "$LOG_FILE" || true
    exit 1
  fi
  TUNNEL_URL="$(grep -Eo 'https://[[:alnum:]-]+\.trycloudflare\.com' "$LOG_FILE" | tail -n 1 || true)"
  if [[ -n "$TUNNEL_URL" ]]; then break; fi
  sleep 2
done

if [[ -z "$TUNNEL_URL" ]]; then
  log 'Timed out waiting for the trycloudflare.com URL. Recent cloudflared output:'
  tail -n 100 "$LOG_FILE" || true
  exit 1
fi

log "Generated public Cloudflare Try URL: $TUNNEL_URL"

# Update .env with the generated Cloudflare Try URL.
# Never overwrite BASE_URL / BACKEND_URL when they already hold a permanent (non-trycloudflare) domain.
python3 - "$SCRIPT_DIR/.env" "$TUNNEL_URL" <<'PY'
import json, pathlib, re, sys
path = pathlib.Path(sys.argv[1])
url = sys.argv[2]

def is_try(v: str) -> bool:
    v = (v or "").strip().strip('"').strip("'").lower()
    return "trycloudflare.com" in v

def is_local(v: str) -> bool:
    v = (v or "").strip().strip('"').strip("'").lower()
    return v.startswith("http://localhost") or v.startswith("http://127.0.0.1") or not v

lines = path.read_text().splitlines() if path.exists() else []
new_lines = []
found_base = found_backend = found_cf = False
for line in lines:
    if re.match(r'^\s*BASE_URL\s*=', line):
        if not found_base:
            raw = line.split('=', 1)[1] if '=' in line else ''
            if is_local(raw) or is_try(raw):
                new_lines.append('BASE_URL=' + json.dumps(url))
            else:
                new_lines.append(line)  # keep permanent domain
            found_base = True
    elif re.match(r'^\s*BACKEND_URL\s*=', line):
        if not found_backend:
            raw = line.split('=', 1)[1] if '=' in line else ''
            if is_local(raw) or is_try(raw):
                new_lines.append('BACKEND_URL=' + json.dumps(url))
            else:
                new_lines.append(line)  # keep permanent domain
            found_backend = True
    elif re.match(r'^\s*CLOUDFLARE_URL\s*=', line):
        if not found_cf:
            new_lines.append('CLOUDFLARE_URL=' + json.dumps(url))
            found_cf = True
    else:
        new_lines.append(line)
if not found_base:
    new_lines.append('BASE_URL=' + json.dumps(url))
if not found_backend:
    new_lines.append('BACKEND_URL=' + json.dumps(url))
if not found_cf:
    new_lines.append('CLOUDFLARE_URL=' + json.dumps(url))
path.write_text('\n'.join(new_lines) + '\n')
path.chmod(0o600)
PY

# Persist the Cloudflare Try URL in MongoDB.
# If permanent_backend_url / a non-trycloudflare backend_url is already set, only update cloudflare_url
# and do NOT rewrite catalog buttons onto the temporary host.
if ! "${COMPOSE[@]}" exec -T web python -c '
import asyncio, datetime, os, sys
from motor.motor_asyncio import AsyncIOMotorClient
from app.utils.helpers import is_trycloudflare_url, rewrite_page_groups_to_cloudflare

async def main():
    uri = os.environ.get("MONGODB_URI") or os.environ.get("DATABASE_URL")
    if not uri:
        raise RuntimeError("MONGODB_URI/DATABASE_URL is missing inside web container")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=15000)
    await client.admin.command("ping")
    db = client[os.environ.get("DATABASE_NAME", "tg_media_bot")]
    now = datetime.datetime.now(datetime.timezone.utc)
    tunnel_url = sys.argv[1].strip().rstrip("/")

    # Permanent only when admin explicitly set permanent_backend_url
    permanent = False
    doc = await db.settings.find_one({"key": "permanent_backend_url"}, {"value": 1})
    val = (doc or {}).get("value")
    if val and not is_trycloudflare_url(str(val)):
        permanent = True

    # Always store the latest Quick Tunnel under cloudflare_url
    await db.settings.update_one(
        {"key": "cloudflare_url"},
        {"$set": {"value": tunnel_url, "updated_by": "cloudflare-deployment", "updated_at": now}},
        upsert=True,
    )

    if not permanent:
        for key in ("base_url", "backend_url"):
            await db.settings.update_one(
                {"key": key},
                {"$set": {"value": tunnel_url, "updated_by": "cloudflare-deployment", "updated_at": now}},
                upsert=True,
            )
        async for doc in db.catalog_pages.find({"groups.0": {"$exists": True}}, {"_id": 1, "groups": 1}):
            old_groups = doc.get("groups") or []
            new_groups = rewrite_page_groups_to_cloudflare(old_groups, tunnel_url)
            if new_groups != old_groups:
                await db.catalog_pages.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"groups": new_groups, "updated_at": now}},
                )

    web_doc = await db.settings.find_one({"key": "website_urls"}, {"value": 1})
    if web_doc and isinstance(web_doc.get("value"), list):
        cleaned = [u for u in web_doc["value"] if not is_trycloudflare_url(str(u))]
        await db.settings.update_one(
            {"key": "website_urls"},
            {"$set": {"value": cleaned, "updated_at": now}},
        )
    client.close()

asyncio.run(main())
' "$TUNNEL_URL"; then
  log 'Could not persist Cloudflare URL via compose exec; internal FastAPI log watcher will synchronize it automatically.'
fi

log "Auto-set Cloudflare URL in .env and MongoDB: $TUNNEL_URL"
log "All download links, stream links, and title-page button URLs now use this Cloudflare Try URL, while Vercel serves the website."

# Quick Tunnels are ephemeral and can occasionally lose their edge-to-origin connection.
# Watch the public health endpoint; after repeated failures exit so systemd recreates the
# tunnel, obtains a fresh hostname, and synchronizes it to MongoDB/.env automatically.
FAILURES=0
while kill -0 "$CF_PID" 2>/dev/null; do
  sleep 20
  if ! kill -0 "$CF_PID" 2>/dev/null; then
    break
  fi
  HTTP_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 8 --max-time 15 "$TUNNEL_URL/health" 2>/dev/null || true)"
  if [[ "$HTTP_CODE" == "200" ]]; then
    FAILURES=0
  else
    FAILURES=$((FAILURES + 1))
    log "Public tunnel health check failed (HTTP ${HTTP_CODE:-no-response}, ${FAILURES}/3)."
    if (( FAILURES >= 3 )); then
      log 'Public tunnel is unhealthy; restarting it through systemd to obtain a fresh URL.'
      kill "$CF_PID" 2>/dev/null || true
      wait "$CF_PID" 2>/dev/null || true
      exit 1
    fi
  fi
done
log 'cloudflared process exited; systemd will restart the tunnel and resynchronize its URL.'
exit 1
