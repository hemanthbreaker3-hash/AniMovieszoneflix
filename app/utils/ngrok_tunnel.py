from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import re

from app.database.connection import db, settings, settings_col
from app.utils.helpers import normalize_public_base_url, rewrite_page_groups_to_cloudflare, is_trycloudflare_url

logger = logging.getLogger(__name__)
_monitor_task: asyncio.Task | None = None
_ngrok_tunnel = None
_last_synced_url: str | None = None


def _update_env_file_urls(tunnel_url: str) -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text().splitlines()
        keys = {"BACKEND_URL": False, "NGROK_URL": False}
        output = []
        for line in lines:
            key = line.split("=", 1)[0].strip() if "=" in line else ""
            if key in keys:
                if not keys[key]:
                    output.append(f'{key}="{tunnel_url}"')
                    keys[key] = True
            elif key == "BASE_URL":
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                if raw.startswith(("http://localhost", "http://127.0.0.1")) or is_trycloudflare_url(raw):
                    output.append(f'BASE_URL="{tunnel_url}"')
                else:
                    output.append(line)
            else:
                output.append(line)
        for key, found in keys.items():
            if not found:
                output.append(f'{key}="{tunnel_url}"')
        env_path.write_text("\n".join(output) + "\n")
    except Exception as exc:
        logger.warning("Could not persist ngrok URL to .env: %s", exc)


async def apply_auto_cloudflare_url(raw_url: str, source: str = "auto-ngrok") -> str | None:
    """Persist an ngrok URL and rewrite generated media buttons to its current domain."""
    global _last_synced_url
    try:
        tunnel_url = normalize_public_base_url(raw_url)
    except ValueError:
        return None
    if not is_trycloudflare_url(tunnel_url):
        return None

    settings.BACKEND_URL = tunnel_url
    os.environ["BACKEND_URL"] = tunnel_url
    os.environ["NGROK_URL"] = tunnel_url
    if not settings.BASE_URL or settings.BASE_URL.startswith(("http://localhost", "http://127.0.0.1")) or is_trycloudflare_url(settings.BASE_URL):
        settings.BASE_URL = tunnel_url
        os.environ["BASE_URL"] = tunnel_url
    if _last_synced_url == tunnel_url:
        return tunnel_url

    now = datetime.now(timezone.utc)
    try:
        for key in ("ngrok_url", "backend_url", "base_url"):
            await settings_col.update_one(
                {"key": key},
                {"$set": {"value": tunnel_url, "updated_by": source, "updated_at": now}},
                upsert=True,
            )
        # Keep legacy key in sync for older panel/API clients.
        await settings_col.update_one(
            {"key": "cloudflare_url"},
            {"$set": {"value": tunnel_url, "updated_by": source, "updated_at": now}},
            upsert=True,
        )
        web_doc = await settings_col.find_one({"key": "website_urls"}, {"value": 1})
        if web_doc and isinstance(web_doc.get("value"), list):
            cleaned = [u for u in web_doc["value"] if not is_trycloudflare_url(str(u))]
            if cleaned != web_doc["value"]:
                await settings_col.update_one({"key": "website_urls"}, {"$set": {"value": cleaned, "updated_at": now}})
        rewritten = 0
        async for doc in db.catalog_pages.find({"groups.0": {"$exists": True}}, {"_id": 1, "groups": 1}):
            old = doc.get("groups") or []
            new = rewrite_page_groups_to_cloudflare(old, tunnel_url)
            if new != old:
                await db.catalog_pages.update_one({"_id": doc["_id"]}, {"$set": {"groups": new, "updated_at": now}})
                rewritten += 1
        _update_env_file_urls(tunnel_url)
        _last_synced_url = tunnel_url
        logger.info("Synced ngrok URL (%s): %s; updated %d catalog pages", source, tunnel_url, rewritten)
    except Exception as exc:
        logger.warning("Could not persist ngrok URL to MongoDB: %s", exc)
    return tunnel_url



def _extract_tunnel_url_from_logs() -> str | None:
    """Compatibility helper: return the currently configured ngrok URL."""
    return os.environ.get("NGROK_URL") or _last_synced_url

async def _ngrok_monitor_loop() -> None:
    global _ngrok_tunnel
    await asyncio.sleep(2)
    token = (os.environ.get("NGROK_AUTHTOKEN") or "").strip()
    if not token:
        logger.warning("NGROK_AUTHTOKEN is not set; automatic public tunnel is disabled. Add your ngrok auth token to .env.")
        return
    try:
        from pyngrok import ngrok
        port = int(os.environ.get("PORT", settings.PORT or 8000))
        def open_tunnel():
            ngrok.set_auth_token(token)
            return ngrok.connect(addr=port, proto="http")
        _ngrok_tunnel = await asyncio.to_thread(open_tunnel)
        url = getattr(_ngrok_tunnel, "public_url", None)
        if url:
            await apply_auto_cloudflare_url(url, source="ngrok-startup")
            logger.info("ngrok public URL: %s", url)
        while True:
            await asyncio.sleep(30)
            try:
                tunnels = await asyncio.to_thread(ngrok.get_tunnels)
                active = next((t for t in tunnels if getattr(t, "public_url", None) == url), None)
                if not active:
                    _ngrok_tunnel = await asyncio.to_thread(open_tunnel)
                    url = getattr(_ngrok_tunnel, "public_url", None)
                    if url:
                        await apply_auto_cloudflare_url(url, source="ngrok-reconnect")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("ngrok monitor error: %s", exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("Could not start ngrok tunnel: %s", exc)


def start_cloudflare_auto_tunnel() -> None:
    """Compatibility name retained for app startup; starts ngrok, not Cloudflare."""
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(_ngrok_monitor_loop())


async def stop_cloudflare_auto_tunnel() -> None:
    global _monitor_task, _ngrok_tunnel
    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
    _monitor_task = None
    if _ngrok_tunnel is not None:
        try:
            from pyngrok import ngrok
            await asyncio.to_thread(ngrok.disconnect, _ngrok_tunnel.public_url)
            await asyncio.to_thread(ngrok.kill)
        except Exception:
            pass
        _ngrok_tunnel = None
