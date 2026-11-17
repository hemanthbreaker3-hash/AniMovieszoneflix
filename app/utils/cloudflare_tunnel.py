from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil

from app.database.connection import db, settings, settings_col
from app.utils.helpers import (
    is_trycloudflare_url,
    normalize_public_base_url,
    rewrite_page_groups_to_cloudflare,
)

logger = logging.getLogger(__name__)

_TUNNEL_REGEX = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
_monitor_task: asyncio.Task | None = None
_cloudflared_proc: asyncio.subprocess.Process | None = None
_last_synced_url: str | None = None


def _update_env_file_urls(tunnel_url: str) -> None:
    """Persist the auto-detected Cloudflare try URL to .env if writable.

    Never overwrites BASE_URL / BACKEND_URL when they already hold a permanent
    (non-trycloudflare) domain — those must stay fixed for permanent /dl links.
    """
    env_path = Path(".env")
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text().splitlines()
        new_lines = []
        found_backend = False
        found_cf = False
        for line in lines:
            if re.match(r"^\s*BACKEND_URL\s*=", line):
                raw_val = line.split("=", 1)[1].strip().strip('"').strip("'") if "=" in line else ""
                if not found_backend:
                    # Keep permanent domain; only replace localhost or previous trycloudflare
                    if not raw_val or raw_val.startswith(("http://localhost", "http://127.0.0.1")) or is_trycloudflare_url(raw_val):
                        new_lines.append("BACKEND_URL=" + json.dumps(tunnel_url))
                    else:
                        new_lines.append(line)
                    found_backend = True
            elif re.match(r"^\s*CLOUDFLARE_URL\s*=", line):
                if not found_cf:
                    new_lines.append("CLOUDFLARE_URL=" + json.dumps(tunnel_url))
                    found_cf = True
            elif re.match(r"^\s*BASE_URL\s*=", line):
                raw_val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if raw_val.startswith(("http://localhost", "http://127.0.0.1")) or is_trycloudflare_url(raw_val):
                    new_lines.append("BASE_URL=" + json.dumps(tunnel_url))
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        if not found_backend:
            new_lines.append("BACKEND_URL=" + json.dumps(tunnel_url))
        if not found_cf:
            new_lines.append("CLOUDFLARE_URL=" + json.dumps(tunnel_url))
        env_path.write_text("\n".join(new_lines) + "\n")
    except Exception as exc:
        logger.debug("Could not update .env with tunnel URL: %s", exc)


async def _has_permanent_backend() -> bool:
    """True only when admin explicitly set permanent_backend_url (fixed domain mode).

    Stale BASE_URL/BACKEND_URL must not block the live trycloudflare.com tunnel from
    powering download and watch links.
    """
    try:
        doc = await settings_col.find_one({"key": "permanent_backend_url"}, {"value": 1})
        val = (doc or {}).get("value")
        if val and not is_trycloudflare_url(str(val)):
            try:
                normalize_public_base_url(str(val))
                return True
            except ValueError:
                return False
    except Exception:
        pass
    return False


async def apply_auto_cloudflare_url(raw_url: str, source: str = "auto-cloudflare") -> str | None:
    """
    Save a detected Cloudflare trycloudflare.com URL for fallback use.

    If a permanent (non-trycloudflare) backend domain is already configured, the temporary
    Quick Tunnel URL is stored only under cloudflare_url and is NOT written over
    backend_url / base_url / permanent_backend_url — so existing /dl and /stream links
    keep working on the fixed domain.
    """
    global _last_synced_url
    try:
        tunnel_url = normalize_public_base_url(raw_url)
    except ValueError:
        return None

    if not is_trycloudflare_url(tunnel_url):
        return None

    permanent = await _has_permanent_backend()

    # Always record the latest Quick Tunnel under cloudflare_url (fallback / recovery)
    os.environ["CLOUDFLARE_URL"] = tunnel_url

    if not permanent:
        # No fixed domain yet — use the Quick Tunnel as the active public origin
        settings.BACKEND_URL = tunnel_url
        os.environ["BACKEND_URL"] = tunnel_url
        if (
            not settings.BASE_URL
            or settings.BASE_URL.startswith(("http://localhost", "http://127.0.0.1"))
            or is_trycloudflare_url(settings.BASE_URL)
        ):
            settings.BASE_URL = tunnel_url
            os.environ["BASE_URL"] = tunnel_url

    if _last_synced_url == tunnel_url:
        return tunnel_url

    now = datetime.now(timezone.utc)
    try:
        # Always update the temporary tunnel slot
        await settings_col.update_one(
            {"key": "cloudflare_url"},
            {"$set": {"value": tunnel_url, "updated_by": source, "updated_at": now}},
            upsert=True,
        )

        if not permanent:
            for key in ("backend_url", "base_url"):
                await settings_col.update_one(
                    {"key": key},
                    {"$set": {"value": tunnel_url, "updated_by": source, "updated_at": now}},
                    upsert=True,
                )
        # When permanent is set, leave backend_url / base_url / permanent_backend_url untouched

        # Keep website_urls exclusively for Vercel / non-tunnel website domains
        web_doc = await settings_col.find_one({"key": "website_urls"}, {"value": 1})
        if web_doc and isinstance(web_doc.get("value"), list):
            cleaned_web = [u for u in web_doc["value"] if not is_trycloudflare_url(str(u))]
            if cleaned_web != web_doc["value"]:
                await settings_col.update_one(
                    {"key": "website_urls"},
                    {"$set": {"value": cleaned_web, "updated_at": now}},
                )

        rewritten_pages = 0
        # Only rewrite catalog button domains when Quick Tunnel is the active public origin.
        # With a permanent domain, never rotate buttons onto a temporary trycloudflare host.
        if not permanent:
            cursor = db.catalog_pages.find({"groups.0": {"$exists": True}}, {"_id": 1, "groups": 1})
            async for doc in cursor:
                old_groups = doc.get("groups") or []
                new_groups = rewrite_page_groups_to_cloudflare(old_groups, tunnel_url)
                if new_groups != old_groups:
                    await db.catalog_pages.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"groups": new_groups, "updated_at": now}},
                    )
                    rewritten_pages += 1
            _update_env_file_urls(tunnel_url)
        else:
            # Permanent domain mode: only record CLOUDFLARE_URL in env, never BASE/BACKEND
            try:
                env_path = Path(".env")
                if env_path.exists():
                    lines = env_path.read_text().splitlines()
                    new_lines, found_cf = [], False
                    for line in lines:
                        if re.match(r"^\s*CLOUDFLARE_URL\s*=", line):
                            if not found_cf:
                                new_lines.append("CLOUDFLARE_URL=" + json.dumps(tunnel_url))
                                found_cf = True
                        else:
                            new_lines.append(line)
                    if not found_cf:
                        new_lines.append("CLOUDFLARE_URL=" + json.dumps(tunnel_url))
                    env_path.write_text("\n".join(new_lines) + "\n")
            except Exception as exc:
                logger.debug("Could not update CLOUDFLARE_URL in .env: %s", exc)

        _last_synced_url = tunnel_url
        logger.info(
            "Auto-set Cloudflare URL (%s): %s (permanent_backend=%s, rewrote button domains on %d catalog pages)",
            source,
            tunnel_url,
            permanent,
            rewritten_pages,
        )
    except Exception as exc:
        logger.warning("Failed to persist auto Cloudflare URL to MongoDB: %s", exc)

    return tunnel_url


def _extract_tunnel_url_from_logs() -> str | None:
    """Check known cloudflared log locations for the most recent trycloudflare.com URL."""
    candidates = [
        Path("cloudflared-quick-tunnel.log"),
        Path("/app/cloudflared-quick-tunnel.log"),
        Path("/tmp/cloudflared-quick-tunnel.log"),
    ]
    for path in candidates:
        try:
            if path.exists():
                content = path.read_text(errors="ignore")
                matches = _TUNNEL_REGEX.findall(content)
                if matches:
                    return matches[-1]
        except Exception:
            continue
    return None


async def _spawn_internal_cloudflared(port: int) -> None:
    """Spawn cloudflared inside the container/process if no external host tunnel log is present."""
    global _cloudflared_proc
    cf_bin = shutil.which("cloudflared") or (
        "/usr/local/bin/cloudflared" if os.path.exists("/usr/local/bin/cloudflared") else None
    )
    if not cf_bin:
        return

    log_path = Path("/tmp/cloudflared-quick-tunnel.log")
    # Prefer public VPS IP from credentials: export TRYURL=... or .env TRYURL=...
    origin_host = (
        os.environ.get("TRYURL")
        or os.environ.get("tryurl")
        or os.environ.get("TRY_URL")
        or os.environ.get("TUNNEL_ORIGIN_HOST")
        or getattr(settings, "TRYURL", None)
        or "138.68.175.206"
    )
    origin_host = str(origin_host).strip() or "138.68.175.206"
    origin_url = f"http://{origin_host}:{port}"
    try:
        log_file = log_path.open("w", encoding="utf-8")
        _cloudflared_proc = await asyncio.create_subprocess_exec(
            cf_bin,
            "tunnel",
            "--no-autoupdate",
            "--loglevel",
            "info",
            "--url",
            origin_url,
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
        )
        logger.info(
            "Started internal cloudflared Quick Tunnel origin=%s (pid=%s)",
            origin_url,
            _cloudflared_proc.pid,
        )
    except Exception as exc:
        logger.warning("Could not start internal cloudflared process: %s", exc)


async def _cloudflare_monitor_loop() -> None:
    """Background loop that auto-detects, spawns if needed, and syncs the trycloudflare.com URL to MongoDB."""
    port = int(os.environ.get("PORT", settings.PORT or 8000))
    await asyncio.sleep(2)

    # First check if a trycloudflare URL is already in environment or host log
    for env_candidate in (os.environ.get("CLOUDFLARE_URL"), settings.BACKEND_URL, settings.BASE_URL):
        if env_candidate and is_trycloudflare_url(env_candidate):
            await apply_auto_cloudflare_url(env_candidate, source="startup-env")
            break

    log_url = _extract_tunnel_url_from_logs()
    if log_url:
        await apply_auto_cloudflare_url(log_url, source="host-log")
    elif os.environ.get("DISABLE_AUTO_CLOUDFLARED", "").lower() not in ("1", "true", "yes"):
        # Wait briefly in case host systemd service is writing cloudflared-quick-tunnel.log
        await asyncio.sleep(3)
        log_url = _extract_tunnel_url_from_logs()
        if log_url:
            await apply_auto_cloudflare_url(log_url, source="host-log")
        elif not _last_synced_url:
            await _spawn_internal_cloudflared(port)

    while True:
        try:
            url = _extract_tunnel_url_from_logs()
            if url:
                await apply_auto_cloudflare_url(url, source="log-watcher")
            elif _cloudflared_proc and _cloudflared_proc.returncode is not None:
                logger.warning("Internal cloudflared exited with code %s; restarting...", _cloudflared_proc.returncode)
                await _spawn_internal_cloudflared(port)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("Cloudflare monitor check error: %s", exc)
        await asyncio.sleep(10)


def start_cloudflare_auto_tunnel() -> None:
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(_cloudflare_monitor_loop())


async def stop_cloudflare_auto_tunnel() -> None:
    global _monitor_task, _cloudflared_proc
    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
    _monitor_task = None
    if _cloudflared_proc and _cloudflared_proc.returncode is None:
        try:
            _cloudflared_proc.terminate()
            await asyncio.wait_for(_cloudflared_proc.wait(), timeout=5)
        except Exception:
            pass
    _cloudflared_proc = None
