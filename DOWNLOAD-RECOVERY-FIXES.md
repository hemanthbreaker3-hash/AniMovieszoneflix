# Download and Cloudflare recovery

- Public `/dl/` and `/stream/` responses continue to advertise byte ranges and exact content lengths for browsers and download managers.
- Telegram transfers now reopen from the first byte not yet sent after transient Telegram/DC interruptions, with bounded exponential retries. Existing HTTP Range requests remain compatible with manual and automatic resume in Chrome, 1DM, ADM, and IDM.
- **Speed & response upgrades (FileToLink-inspired):** Higher concurrent client capacity (48), more parallel stripe bots (8), keep-alive connections, and short public Cache-Control so download managers and browsers get faster first-byte response and higher sustained throughput while preserving the exact `/dl/<code>` and `/stream/<code>` URL structure.
- **Buffering fixes (watch + external players):** `Connection: keep-alive` + `Keep-Alive` + `X-Accel-Buffering: no` + `Pragma: public` reduce stalls in VLC / MX Player / 1DM. Built-in watch player uses `preload="auto"`, detects prolonged buffering, and nudges a tiny seek to re-request the current Range so playback recovers without a full reload. Unknown video files on `/stream/` get a proper `video/mp4` Content-Type when needed.
- Cloudflare Quick Tunnel uses HTTP/2 to the edge and checks its public `/health` endpoint. After three consecutive failures (including Cloudflare 520/502/521/522 responses), the tunnel service exits so systemd can recreate the temporary tunnel and synchronize its newly assigned URL to MongoDB and `.env`.
- Uvicorn trusts forwarded headers from the container ingress so generated public links and request scheme handling remain consistent.

## Permanent download / stream links (required for production)

`trycloudflare.com` Quick Tunnels are **temporary**. When the tunnel restarts the hostname changes and old `/dl/` + `/stream/` links break.

**Short codes themselves never expire** (files are stored with `expiry_time=null`). Links only break when the **public hostname** rotates.

### Make links permanent

1. Point a fixed domain at your VPS streamer, e.g. Cloudflare **Named Tunnel**:
   - Create a Named Tunnel in the Cloudflare Zero Trust dashboard
   - Public hostname: `dl.yourdomain.com` → service `http://127.0.0.1:8000`
2. Tell the bot the permanent host (admin only):

```text
/backendurl https://dl.yourdomain.com
```
or
```text
/baseurl https://dl.yourdomain.com
```

3. The bot stores this as `permanent_backend_url` + `backend_url`. After that:
   - All new `/dl/<code>` and `/stream/<code>` links use `https://dl.yourdomain.com`
   - Quick Tunnel auto-updates **cannot** overwrite the permanent domain
   - Catalog button URLs are rewritten once to the permanent host and stay there

Until a permanent domain is set, the app falls back to the current Quick Tunnel URL for convenience during setup only.
