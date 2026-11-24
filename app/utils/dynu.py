"""Dynu DDNS updater and auto-activation helpers."""
from __future__ import annotations
import asyncio, datetime, ipaddress, logging, os, urllib.parse, urllib.request
from app.database.connection import settings, settings_col
logger=logging.getLogger(__name__)
_task=None; _stop=False
_IP_ENDPOINTS=("https://api.ipify.org","https://ifconfig.me/ip","https://icanhazip.com","https://checkip.amazonaws.com")
def normalize_dynu_host(raw:str|None)->str:
    val=(raw or '').strip().lower()
    if val.startswith(('http://','https://')): val=urllib.parse.urlparse(val).hostname or ''
    return val.strip().strip('.')
def _public_ip():
    for url in _IP_ENDPOINTS:
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'Dynu-Updater/1.0'})
            with urllib.request.urlopen(req,timeout=6) as r: ip=r.read().decode().strip()
            ipaddress.ip_address(ip); return ip
        except Exception: continue
    return None
async def get_public_ip(): return await asyncio.to_thread(_public_ip)
async def get_config():
    try:
        docs={d['key']:d.get('value','') for d in await settings_col.find({'key':{'$in':['dynu_hostname','dynu_username','dynu_password']}}).to_list(length=10)}
    except Exception: docs={}
    return (normalize_dynu_host(docs.get('dynu_hostname') or os.getenv('DYNU_HOSTNAME')),str(docs.get('dynu_username') or os.getenv('DYNU_USERNAME') or '').strip(),str(docs.get('dynu_password') or os.getenv('DYNU_PASSWORD') or '').strip())
def _update(host,user,password,ip):
    qs=urllib.parse.urlencode({'hostname':host,'myip':ip or ''})
    req=urllib.request.Request('https://api.dynu.com/nic/update?'+qs,headers={'User-Agent':'Dynu-Updater/1.0'})
    import base64
    req.add_header('Authorization','Basic '+base64.b64encode(f'{user}:{password}'.encode()).decode())
    with urllib.request.urlopen(req,timeout=15) as r: result=r.read().decode(errors='replace').strip()
    return result.lower() in ('good','nochg'),result
async def update_once(host,user,password,ip=None):
    host=normalize_dynu_host(host); user=(user or '').strip(); password=(password or '').strip()
    if not host or not user or not password: return False,'Missing hostname, username or password',None
    ip=(ip or await get_public_ip() or '').strip()
    try:
        ok,res=await asyncio.to_thread(_update,host,user,password,ip)
        return ok,res,ip or None
    except Exception as e: return False,str(e),ip or None
async def probe(host,port):
    p=int(port or 8000); candidates=[f'https://{host}',f'http://{host}']
    for x in (p,8000,8001): candidates.append(f'http://{host}:{x}')
    for url in candidates:
        try:
            req=urllib.request.Request(url.rstrip('/')+'/health',headers={'User-Agent':'Dynu-Probe/1.0'})
            def check():
                with urllib.request.urlopen(req,timeout=4) as r:return 200<=r.status<400
            if await asyncio.to_thread(check):return url,True
        except Exception: pass
    return (f'http://{host}' if p==80 else f'http://{host}:{p}'),False
async def sync_activate(updated_by=None):
    host,user,password=await get_config()
    if not host or not user or not password:return False,'Not configured',None,None,False
    ok,res,ip=await update_once(host,user,password)
    if not ok:return False,res,ip,None,False
    port=int(str(os.getenv('PORT') or getattr(settings,'PORT',None) or '8000'))
    url,reachable=await probe(host,port); now=datetime.datetime.utcnow(); meta={'value':url,'updated_at':now}
    if updated_by is not None:meta['updated_by']=updated_by
    await settings_col.update_one({'key':'dynu_url'},{'$set':meta},upsert=True)
    if reachable:
        settings.BACKEND_URL=url;os.environ['BACKEND_URL']=url
        await settings_col.update_one({'key':'backend_url'},{'$set':meta},upsert=True)
        base=await settings_col.find_one({'key':'base_url'}) or {}; current=str(base.get('value') or '').lower()
        if not ('.pages.dev' in current or (current.startswith(('http://','https://')) and 'vercel.app' not in current and 'trycloudflare.com' not in current and host not in current)):
            settings.BASE_URL=url;os.environ['BASE_URL']=url
            await settings_col.update_one({'key':'base_url'},{'$set':meta},upsert=True)
        try:
            from app.cms import rewrite_all_stored_catalog_buttons
            await rewrite_all_stored_catalog_buttons(url)
        except Exception:logger.exception('Dynu catalog URL rewrite failed')
    return True,res,ip,url,reachable
async def _loop():
    global _stop
    while not _stop:
        try:
            host,user,password=await get_config()
            if host and user and password:
                ok,res,ip,url,reach=await sync_activate()
                if ok:logger.info('Dynu DDNS updated %s -> %s; reachable=%s',host,ip,reach)
                else:logger.warning('Dynu update failed for %s: %s',host,res)
        except asyncio.CancelledError:break
        except Exception:logger.exception('Dynu loop error')
        for _ in range(300):
            if _stop:break
            await asyncio.sleep(1)
def start_updater():
    global _task,_stop
    _stop=False
    if _task is None or _task.done():_task=asyncio.create_task(_loop())
async def stop_updater():
    global _task,_stop
    _stop=True
    if _task and not _task.done():_task.cancel()
    _task=None
