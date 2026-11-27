#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OP7 FB/EB + Pingbo live read-only refresher.

Only allow-listed public event-list / event-detail / odds-read endpoints are called.
No bet, order, wallet, deposit, withdrawal, cashout, or account mutation endpoints.
Uses normal Playwright persistent browser profiles; no CAPTCHA/MFA bypass.
"""
import argparse, base64, gzip, json, os, sys, time, threading, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import op7_fb_eb_venue_mapper as mapper

ENDPOINTS=json.loads((Path(__file__).with_name('endpoints.json')).read_text(encoding='utf-8'))
SITES={
 'OP7_WEB':ENDPOINTS['web']['op7']['base_url'],
 'FB_ORIGIN':ENDPOINTS['apis']['fb']['base_url'],
 'EB_ORIGIN':ENDPOINTS['apis']['eb']['base_url'],
 'PINGBO_WEB':ENDPOINTS['web']['pingbo']['base_url'],
 'PINGBO_ORIGIN':ENDPOINTS['apis']['pingbo']['base_url'],
}
PINGBO_HOSTS=set(ENDPOINTS['apis']['pingbo']['hosts'])
ALLOW={}
for api in ENDPOINTS['apis'].values():
 for host in api.get('hosts',[]):
  ALLOW[host]=set(api.get('read_paths',[]))

def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds')
def headers_env(name):
 raw=os.environ.get(name,'')
 if not raw: return {'Accept':'application/json','User-Agent':'Mozilla/5.0 ReadOnlyMonitor/1.0'}
 h=json.loads(raw)
 if not isinstance(h,dict): raise ValueError(f'{name} must be a JSON object')
 return {k:v for k,v in h.items() if isinstance(k,str) and isinstance(v,str) and k.lower() not in {'cookie','host','content-length','connection','transfer-encoding'}}

def _decode_b64_gzip_json(value):
 """Clean and decode application-level Base64 + gzip/zlib JSON, if present."""
 if not isinstance(value,str): return value
 encoded=''.join(value.split()).replace('-','+').replace('_','/')
 if not encoded: return value
 try:
  packed=base64.b64decode(encoded+'='*(-len(encoded)%4),validate=True)
 except Exception:
  return value
 try:
  try:
   decoded=gzip.decompress(packed)
  except (OSError,EOFError):
   import zlib
   decoded=zlib.decompress(packed)
  text=decoded.decode('utf-8')
  try:
   return json.loads(text)
  except json.JSONDecodeError:
   return json.loads(urllib.parse.unquote(text))
 except Exception:
  # Leave ordinary strings untouched; callers can detect an undecoded
  # Base64-looking payload and report a sanitized diagnostic.
  return value

def unwrap(obj):
 """Normalize an API envelope so its data field is decoded exactly once."""
 if isinstance(obj,str):
  return _decode_b64_gzip_json(obj)
 if isinstance(obj,dict) and isinstance(obj.get('data'),str):
  original=obj['data']
  decoded=_decode_b64_gzip_json(original)
  if decoded is not original:
   obj=dict(obj); obj['data']=decoded
 return obj

class AuthenticationRequired(RuntimeError):
 def __init__(self, site, status, url):
  self.site=site; self.status=status; self.url=url
  super().__init__(f'{site} HTTP {status}: 登录会话失效或无权访问；请使用 --setup-session {site} 重新登录并检查账号权限')

# Exact request templates observed in the already-authenticated visible browser tabs.
# Cookies stay in Playwright's browser-context cookie jar; no credential is written to disk.
CAPTURED_REQUESTS={}
CAPTURED_ORIGINS={}
USER_INPUT_WAITING=threading.Event()

# EB NBA page lifecycle diagnostics. Only endpoint paths and capture state are logged.
EB_NBA_PAGE_ENDPOINTS=(
 '/yewu11/pub/v1/m/menu/initPB',
 '/yewu11/v1/m/esportsMatchesPB',
 '/yewu11/v1/m/getFilterMatchListPB',
 '/yewu11/v1/m/getMatchBaseInfoByMidsPB',
 '/yewu11/v1/m/matchesPagePB',
 '/yewu11/v1/m/noLiveMatchesPagePB',
)
EB_NBA_CORE_ENDPOINTS=(
 '/yewu11/v1/m/esportsMatchesPB',
 '/yewu11/v1/m/matchesPagePB',
)
EB_NBA_CAPTURE_STATE={
 path:{'seen':False,'captured':False,'method':None}
 for path in EB_NBA_PAGE_ENDPOINTS
}

def eb_nba_capture_status():
 """Return a credential-free snapshot of the six EB NBA page listeners."""
 return {
  path:dict(EB_NBA_CAPTURE_STATE[path])
  for path in EB_NBA_PAGE_ENDPOINTS
 }


def persist_observed_endpoint(api_family, origin, host, path, resource_type=None, method=None):
 """Persist API discovery metadata only; never persist request bodies, headers or credentials."""
 config_path=Path(__file__).with_name('endpoints.json')
 config=json.loads(config_path.read_text(encoding='utf-8'))
 entry={'family':api_family,'origin':origin,'host':host,'path':path,
        'resource_type':resource_type or 'unknown','method':method or 'GET','last_observed_at_utc':now()}
 discovered=config.setdefault('discovered_endpoints',[])
 key=(entry['family'],entry['origin'],entry['path'],entry['method'])
 existing=next((x for x in discovered if (x.get('family'),x.get('origin'),x.get('path'),x.get('method'))==key),None)
 if existing:
  existing.update(entry)
 else:
  discovered.append(entry)
 discovered.sort(key=lambda x:(x.get('family',''),x.get('host',''),x.get('path','')))
 if api_family in ('fb','eb','pingbo','common'):
  api=config.setdefault('apis',{}).setdefault(api_family,{})
  if api_family=='eb' and path.startswith('/yewu13/'):
   api['bet_api_base_url']=origin
   api['base_url']=origin
  elif api_family=='eb' and path.startswith('/yewu11/'):
   api['nba_read_base_url']=origin
  else:
   api['base_url']=origin
  hosts=api.setdefault('hosts',[])
  if host not in hosts:
   hosts.append(host); hosts.sort()
  observed=api.setdefault('observed_paths',[])
  if path not in observed:
   observed.append(path); observed.sort()
 tmp=config_path.with_suffix('.json.tmp')
 tmp.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 tmp.replace(config_path)
 ENDPOINTS.clear(); ENDPOINTS.update(config)


def capture_request(request, family):
 try:
  p=urllib.parse.urlsplit(request.url)
  if p.scheme not in ('http','https') or not p.hostname: return
  resource_type=getattr(request,'resource_type','')
  if resource_type not in ('xhr','fetch'): return
  api_family=('pingbo' if family=='pingbo' else
              'eb' if p.path.startswith(('/yewu11/','/yewu13/')) else
              'fb' if p.path.startswith('/v1/match/') else
              'common' if p.path.startswith('/api/') else 'op7_other')
  origin=f'{p.scheme}://{p.netloc}'
  # Capture the in-memory template before writing discovery metadata. A config
  # write failure must never discard a valid, allow-listed read request.
  eb_api=ENDPOINTS.get('apis',{}).get('eb',{})
  eb_allowed=set(eb_api.get('read_paths',[])+eb_api.get('bet_paths',[]))
  known_paths={path for api in ENDPOINTS.get('apis',{}).values() for path in (api.get('read_paths',[])+api.get('bet_paths',[]))}
  query_params=urllib.parse.parse_qs(p.query, keep_blank_values=True)
  if api_family=='pingbo' and request.method=='POST' and getattr(request,'post_data',None):
   try:
    posted=request.post_data
    if posted.lstrip().startswith('{'):
     body_params=json.loads(posted)
     if isinstance(body_params,dict):
      query_params.update({str(k):[str(v)] for k,v in body_params.items()})
    else:
     query_params.update(urllib.parse.parse_qs(posted, keep_blank_values=True))
   except Exception:
    pass
  required_pingbo_params={
   'sportId':['4'],'leagueIds':['487'],'periodNumber':['0'],
   'periodNumbers':['-1'],'version':['0']}
  pingbo_nba_feed=(api_family=='pingbo'
   and p.path in ('/sports-service/sv/mobile/odds-live','/sports-service/sv/mobile/odds-today')
   and all(query_params.get(k)==v for k,v in required_pingbo_params.items()))
  allowed=(p.path in eb_allowed) if api_family=='eb' else (p.path in known_paths)
  if pingbo_nba_feed:
   allowed=True
  captured=False
  if allowed and request.method in ('GET','POST'):
   key=(api_family,p.path)
   headers={k:v for k,v in request.headers.items()
    if k.lower() not in {'cookie','host','content-length','connection','accept-encoding','transfer-encoding'}
    and not k.lower().startswith('sec-')}
   CAPTURED_REQUESTS[key]={'method':request.method,'url':request.url,'headers':headers,'post_data':request.post_data,'captured_at_monotonic':time.monotonic()}
   captured=True
   if api_family in ('fb','eb','pingbo','common'):
    origin_key='eb_bet' if api_family=='eb' and p.path.startswith('/yewu13/') else api_family
    CAPTURED_ORIGINS[origin_key]=origin
    api=ENDPOINTS.get('apis',{}).get(api_family,{})
    ALLOW[p.netloc]=set(api.get('read_paths',[]))
   if pingbo_nba_feed:
    ALLOW[p.netloc].add(p.path)
  if api_family=='pingbo' and p.path in PINGBO_ODDS_PATHS:
   _analysis_append({'kind':'browser_auto_request','source':'pingbo_page','method':request.method,
    'url_without_query':f'{p.scheme}://{p.netloc}{p.path}','path':p.path,
    'request_params':_request_business_params(request),'resource_type':resource_type,
    'nba_filter':pingbo_nba_feed,'used_as_request_template':captured})
   print('【pingBo】[CAPTURE]',f'method={request.method}',f'path={p.path}',f'sportId={query_params.get("sportId", [])}',f'leagueIds={query_params.get("leagueIds", [])}',f'nba_filter={pingbo_nba_feed}',f'resource_type={resource_type}',f'captured={captured}',flush=True)
  if api_family=='eb' and p.path in EB_NBA_PAGE_ENDPOINTS:
   state=EB_NBA_CAPTURE_STATE[p.path]
   state['seen']=True
   state['captured']=captured
   state['method']=request.method
   print(
    '【EB】[EB_CAPTURE_DEBUG]',
    f'family={family}',
    f'api_family={api_family}',
    f'method={request.method}',
    f'path={p.path}',
    f'allowed={allowed}',
    f'captured={captured}',
    f'resource_type={resource_type}',
    flush=True,
   )
  # Discovery persistence is best-effort and never blocks the live capture.
  try:
   persist_observed_endpoint(api_family,origin,p.netloc,p.path,resource_type,request.method)
  except Exception as persist_exc:
   print(f'浏览器请求已捕获，但端点元数据写入失败：{type(persist_exc).__name__}: {persist_exc}',file=sys.stderr)
 except Exception as exc:
  print(f'浏览器请求捕获/配置更新失败：{type(exc).__name__}: {exc}',file=sys.stderr)


# Local Pingbo page-vs-script API diagnostics. Files stay under --out and are never uploaded.
PINGBO_ANALYSIS_DIR=None
PINGBO_ANALYSIS_FILE=None
PINGBO_ANALYSIS_SEQUENCE=0
PINGBO_ANALYSIS_LAST_UI_HASH=None
PINGBO_ANALYSIS_LAST_UI_AT=None
PINGBO_ANALYSIS_LAST_RESPONSE=None
PINGBO_ANALYSIS_LOCK=threading.Lock()
PINGBO_ODDS_PATHS=('/sports-service/sv/mobile/odds-live','/sports-service/sv/mobile/odds-today')

def _analysis_redact(value):
 if isinstance(value,dict):
  out={}
  for key,item in value.items():
   if any(token in str(key).lower() for token in ('cookie','token','authorization','password','passwd','secret','session','credential')):
    out[str(key)]='[REDACTED]'
   else: out[str(key)]=_analysis_redact(item)
  return out
 if isinstance(value,list): return [_analysis_redact(x) for x in value]
 return value

def _analysis_append(record):
 if PINGBO_ANALYSIS_DIR is None: return
 try:
  PINGBO_ANALYSIS_DIR.mkdir(parents=True,exist_ok=True)
  record={'recorded_at_utc':now(),**_analysis_redact(record)}
  with PINGBO_ANALYSIS_LOCK:
   PINGBO_ANALYSIS_FILE.parent.mkdir(parents=True,exist_ok=True)
  with PINGBO_ANALYSIS_FILE.open('a',encoding='utf-8') as fh:
    fh.write(json.dumps(record,ensure_ascii=False,default=str)+'\n')
 except Exception as exc:
  print(f'[Pingbo分析] 本地日志写入失败：{type(exc).__name__}: {exc}',file=sys.stderr,flush=True)

def _request_business_params(request):
 parsed=urllib.parse.urlsplit(request.url)
 params={'query':urllib.parse.parse_qs(parsed.query,keep_blank_values=True)}
 raw=getattr(request,'post_data',None)
 if raw:
  try:
   body=json.loads(raw)
   params['body']=body if isinstance(body,(dict,list)) else raw
  except Exception:
   params['body']=urllib.parse.parse_qs(raw,keep_blank_values=True) or raw
 return _analysis_redact(params)

def _pingbo_page_response(response, page):
 try:
  parsed=urllib.parse.urlsplit(response.url)
  if parsed.path not in PINGBO_ODDS_PATHS: return
  req=response.request
  try: payload=response.json()
  except Exception:
   try: payload=response.text()
   except Exception as exc: payload={'body_read_error':str(exc)}
  ui_text=''
  try: ui_text=page.locator('body').inner_text(timeout=1500)[:100000]
  except Exception: pass
  global PINGBO_ANALYSIS_LAST_RESPONSE
  PINGBO_ANALYSIS_LAST_RESPONSE={'path':parsed.path,'response_at_utc':now(),'http_status':response.status}
  _analysis_append({'kind':'browser_auto_response','source':'pingbo_page','method':req.method,
   'url_without_query':f'{parsed.scheme}://{parsed.netloc}{parsed.path}',
   'path':parsed.path,'request_params':_request_business_params(req),
   'http_status':response.status,'response_headers':{k:v for k,v in response.headers.items() if k.lower() not in ('set-cookie','authorization')},
   'response_data':payload,'page_visible_text_at_response':ui_text})
 except Exception as exc:
  _analysis_append({'kind':'browser_response_capture_error','error':f'{type(exc).__name__}: {exc}'})

def _capture_pingbo_ui_snapshot(page):
 global PINGBO_ANALYSIS_LAST_UI_HASH,PINGBO_ANALYSIS_LAST_UI_AT
 try:
  visible=page.locator('body').inner_text(timeout=1000)[:100000]
  digest=__import__('hashlib').sha256(visible.encode('utf-8')).hexdigest()
  if digest!=PINGBO_ANALYSIS_LAST_UI_HASH:
   _analysis_append({'kind':'page_visible_text_change','page_url':page.url,'previous_snapshot_at_utc':PINGBO_ANALYSIS_LAST_UI_AT,
    'snapshot_sha256':digest,'visible_text':visible,'latest_odds_response':PINGBO_ANALYSIS_LAST_RESPONSE})
   PINGBO_ANALYSIS_LAST_UI_HASH=digest; PINGBO_ANALYSIS_LAST_UI_AT=now()
 except Exception:
  pass

def _pingbo_rate_limit_info(response):
 """Return Cloudflare 1015 metadata for an HTTP or JSON-level rate-limit response."""
 payload=None
 try:
  payload=response.json()
 except Exception:
  try: payload=json.loads(response.text())
  except Exception: payload=None
 if not isinstance(payload,dict):
  return {'retry_after':30,'error_code':1015,'error_name':'rate_limited'} if response.status==429 else None
 code=payload.get('error_code')
 name=str(payload.get('error_name') or '').lower()
 category=str(payload.get('error_category') or '').lower()
 cloudflare=payload.get('cloudflare_error') is True
 if response.status==429 or str(code)=='1015' or name=='rate_limited' or (category=='rate_limit' and cloudflare):
  try: retry_after=max(1,int(payload.get('retry_after') or 30))
  except (TypeError,ValueError): retry_after=30
  return {'retry_after':retry_after,'error_code':code or 1015,'error_name':name or 'rate_limited'}
 return None


def request_json(ctx,url,method,headers,params=None,body=None,timeout_ms=15000,family=None):
 p=urllib.parse.urlsplit(url)
 if p.scheme!='https' or p.netloc not in ALLOW or p.path not in ALLOW[p.netloc] or method not in ('GET','POST'):
  raise ValueError('Blocked non-allow-listed destination or method')
 template=CAPTURED_REQUESTS.get((family,p.path)) if family else None
 req_headers=dict(template['headers']) if template else dict(headers or {})
 req_headers.update({k:v for k,v in (headers or {}).items() if k.lower() not in {'cookie','host','content-length'}})
 if template:
  observed=urllib.parse.urlsplit(template['url'])
  query=dict(urllib.parse.parse_qsl(observed.query,keep_blank_values=True))
  query.update({k:str(v) for k,v in (params or {}).items()})
  url=f'{CAPTURED_ORIGINS.get(family, p.scheme+"://"+p.netloc)}{p.path}'
  if query: url+='?'+urllib.parse.urlencode(query,doseq=True)
  method=template['method']
  raw=template.get('post_data')
  observed_body={}
  if raw:
   try: observed_body=json.loads(raw)
   except Exception:
    try: observed_body=dict(urllib.parse.parse_qsl(raw,keep_blank_values=True))
    except Exception: observed_body={}
  if body is not None:
   if isinstance(body,dict): observed_body.update(body)
   else: observed_body=body
  body=observed_body if method=='POST' else None
 else:
  if params:
   q=urllib.parse.urlencode(params,doseq=True); url+=('&' if '?' in url else '?')+q

 retry_delays=[30,60,120,240,300]
 retry_count=0
 while True:
  if family=='pingbo' and p.path in PINGBO_ODDS_PATHS:
   assembled=urllib.parse.urlsplit(url)
   _analysis_append({'kind':'script_request','source':'script_assembled','method':method,
    'url_without_query':f'{assembled.scheme}://{assembled.netloc}{assembled.path}','path':p.path,
    'request_params':{'query':urllib.parse.parse_qs(assembled.query,keep_blank_values=True),'body':body if method=='POST' else None},
    'template_used':bool(template),'template_capture_age_seconds':(time.monotonic()-template['captured_at_monotonic']) if template else None})
  if method=='GET': r=ctx.request.get(url,headers=req_headers,timeout=timeout_ms)
  else: r=ctx.request.post(url,headers=req_headers,data=body or {},timeout=timeout_ms)
  if r.status in (401,403):
   site='pingbo' if family=='pingbo' else 'op7'
   raise AuthenticationRequired(site,r.status,f'{p.netloc}{p.path}')
  rate_limit=_pingbo_rate_limit_info(r) if family=='pingbo' else None
  if rate_limit:
   if retry_count>=len(retry_delays):
    print('[Pingbo] 接口请求被限制，已连续重试 5 次仍未恢复；停止本次请求，请检查请求频率后再启动。',file=sys.stderr,flush=True)
    raise RuntimeError(f'Pingbo rate limited: Cloudflare {rate_limit["error_code"]} after {retry_count} retries')
   delay=max(retry_delays[retry_count],rate_limit['retry_after'])
   retry_count+=1
   print(f'[Pingbo] 接口请求被限制（Cloudflare 1015 / HTTP {r.status}），第 {retry_count} 次重试，等待 {delay} 秒后再请求。',file=sys.stderr,flush=True)
   time.sleep(delay)
   continue
  if not r.ok: raise RuntimeError(f'HTTP {r.status} from {p.netloc}{p.path}')
  payload=unwrap(r.json())
  if family=='pingbo' and p.path in PINGBO_ODDS_PATHS:
   _analysis_append({'kind':'script_response','source':'script_assembled','method':method,
    'url_without_query':f'{p.scheme}://{p.netloc}{p.path}','path':p.path,'http_status':r.status,
    'response_data':payload})
  return payload

def data_of(obj):
 return obj.get('data') if isinstance(obj,dict) and 'data' in obj else obj

def fb_events(payload):
 d=data_of(payload); rows=d.get('records',[]) if isinstance(d,dict) else []
 out=[]
 for x in rows if isinstance(rows,list) else []:
  if not isinstance(x,dict): continue
  league_obj=x.get('lg') if isinstance(x.get('lg'),dict) else {}
  league=mapper.first(league_obj,'na','nm','name') or x.get('leagueName') or x.get('league')
  # Drop non-NBA and unclassified competitions before they enter the live cache.
  if not mapper.is_nba_league(league): continue
  nm=str(x.get('nm') or '')
  parts=__import__('re').split(r'\s+vs\.?\s+',nm,maxsplit=1,flags=__import__('re').I)
  if len(parts)==2:
   out.append({'id':str(x.get('id')),'home':parts[0].strip(),'away':parts[1].strip(),'name':nm,
    'league':str(league),'raw_status_ms':x.get('ms'),'phase_pe':(x.get('mc') or {}).get('pe') if isinstance(x.get('mc'),dict) else None})
 return out

def eb_events(payload):
 d=data_of(payload)
 if isinstance(d,dict): d=d.get('data',[])
 out=[]
 for x in d if isinstance(d,list) else []:
  if not isinstance(x,dict): continue
  mid=x.get('mid') or x.get('id'); home=x.get('mhn') or x.get('homeName'); away=x.get('man') or x.get('awayName')
  if mid and home and away: out.append({'id':str(mid),'home':str(home),'away':str(away),'raw_status':x.get('mst'),'phase_mmp':x.get('mmp'),'is_live':x.get('isLive') if isinstance(x.get('isLive'),bool) else None,'period_label':x.get('periodLabel') or x.get('periodName'),'clock_seconds':x.get('clockSeconds') or x.get('elapsedSeconds') or x.get('remainingSeconds'),'league':x.get('tnjc') or x.get('leagueName') or x.get('tn'),'sport':x.get('csna') or x.get('sportName') or x.get('sport'),'start_time_ms':x.get('mgt') or x.get('startTime'),'raw_event_fields':sorted(x.keys())})
 return out

def save_latest(out,name,payload,status='SUCCESS',details=None):
 out.mkdir(parents=True,exist_ok=True)
 stamp=now()
 p=out/(name+'_latest.json')
 tmp=p.with_suffix('.tmp')
 tmp.write_text(json.dumps({'status':status,'fetched_at_utc':stamp,'data':payload,'details':details or {}},ensure_ascii=False,indent=2),encoding='utf-8')
 tmp.replace(p)
 (out/(name+'_status.json')).write_text(json.dumps({'status':status,'observed_at_utc':stamp,'details':details or {},'error':None},ensure_ascii=False,indent=2),encoding='utf-8')

def _market_cache_key(row):
 # Totals are keyed without the line so a changed line replaces the previous line.
 venue=str(row.get('venue') or '')
 event=str(row.get('event_id') or '')
 market_id=str(row.get('market_id') or '')
 market_type=str(row.get('market_type_mty',row.get('play_id_hpid')) or '')
 market_key=str(row.get('market_key') or '')
 label=' '.join(str(row.get(k) or '') for k in ('market_name','market_label','play_id_hpid','market_type_mty')).lower()
 is_total=(market_key=='1' or any(token in label for token in
  ('大小','总分','總分','total','over/under','over under','totals')))
 if is_total:
  return ('TOTAL',venue,event,market_id,market_type,
          str(row.get('market_top_key') or row.get('play_id_hpid') or ''),
          str(row.get('market_name') or ''),market_key,
          str(row.get('side') or ''),str(row.get('selection_id') or ''))
 return ('MARKET',venue,event,market_id,market_type,
         str(row.get('group_id') or ''),str(row.get('line',row.get('line_hv')) or ''),
         market_key,str(row.get('side') or ''),str(row.get('selection_id') or ''))

def update_market_cache(cache, venue, rows, active_event_ids, observed_at):
 # One current record per event/market/selection. Replacing a record refreshes
 # its timestamp whether odds changed or not; no historical copies are retained.
 active={str(x) for x in active_event_ids}
 for key,row in list(cache.items()):
  if key[2] not in active:
   del cache[key]
 for source in rows:
  row=dict(source)
  row['observed_at_utc']=observed_at
  key=_market_cache_key(row)
  cache[key]=row
 return list(cache.values())

def save_failure(out,name,error):
 out.mkdir(parents=True,exist_ok=True)
 status='AUTH_REQUIRED' if isinstance(error,AuthenticationRequired) else 'FAILED'
 (out/(name+'_status.json')).write_text(json.dumps({'status':status,'failed_at_utc':now(),'error_type':type(error).__name__,'error':str(error)},ensure_ascii=False,indent=2),encoding='utf-8')

def setup_session(site,profiles_dir):
 sites={'op7':(SITES['OP7_WEB'],'OP7'),'pingbo':(SITES['PINGBO_WEB'],'Pingbo')}
 targets=list(sites) if site=='both' else [site]
 with sync_playwright() as p:
  for key in targets:
   url,label=sites[key]; profile=Path(profiles_dir)/key; profile.mkdir(parents=True,exist_ok=True)
   print(f'\n正在打开 {label} 可见浏览器。请自行输入账号密码并完成验证码/MFA。')
   print('程序不会读取、记录或上传密码、验证码、Cookie 或 Token。')
   ctx=p.chromium.launch_persistent_context(str(profile),headless=False,args=['--no-sandbox','--disable-dev-shm-usage'])
   try:
    page=ctx.pages[0] if ctx.pages else ctx.new_page()
    try: page.goto(url,wait_until='domcontentloaded',timeout=30000)
    except PlaywrightTimeoutError: print('页面加载等待超时；浏览器仍保持打开，请检查页面后继续手动登录。',file=sys.stderr)
    input(f'请确认 {label} 已成功登录并进入正常业务页面，然后回到终端按 Enter 保存本地会话；若未成功登录请不要按 Enter：')
    cookies=ctx.cookies()
    domains=[c.get('domain','') for c in cookies]
    op_hosts=set(ENDPOINTS['web']['op7']['hosts']) | set(ENDPOINTS['apis']['fb']['hosts']) | set(ENDPOINTS['apis']['eb']['hosts']) | set(ENDPOINTS['apis']['common']['hosts'])
    pb_hosts=set(ENDPOINTS['web']['pingbo']['hosts']) | set(ENDPOINTS['apis']['pingbo']['hosts'])
    relevant=[d for d in domains if any(h.lstrip('.') in d.lstrip('.') for h in op_hosts)] if key=='op7' else [d for d in domains if any(h.lstrip('.') in d.lstrip('.') for h in pb_hosts)]
    print(f'{label}：浏览器配置已保存在 {profile}。当前 Cookie 数量 {len(cookies)}，相关域 Cookie 数量 {len(relevant)}。')
    if not relevant:
     print('警告：未发现相关域 Cookie；若站点使用其他存储方式，会话仍可能已保存。请用后台刷新验证，若返回 401/403 请重新登录。')
    else: print('请妥善保护本地配置目录；其中包含可复用的登录会话，不要上传或提交到 Git。')
   finally: ctx.close()
 print('登录初始化结束。可用 --live 启动后台只读刷新。')

def report_auth_failure(error,args):
 print(f'\n检测到 {error.site.upper()} 返回 HTTP {error.status}。已停止刷新，请重新执行：python op7_fb_eb_pingbo_live_refresh.py --setup-session {error.site} --profiles-dir {args.profiles_dir}',file=sys.stderr)

def main():
 ap=argparse.ArgumentParser(description='Read-only automatic refresh for OP7 FB/EB and Pingbo')
 ap.add_argument('--config',default='refresh_config.json'); ap.add_argument('--policy',default='refresh_policy.json'); ap.add_argument('--profiles-dir',default='browser_profiles')
 ap.add_argument('--setup-session',choices=['op7','pingbo','both'],help='打开可见浏览器，手动登录并保存本地会话')
 ap.add_argument('--out',default='live_snapshots'); ap.add_argument('--live',action='store_true')
 ap.add_argument('--fb-list-interval',type=int); ap.add_argument('--fb-odds-interval',type=int)
 ap.add_argument('--eb-list-interval',type=int); ap.add_argument('--eb-odds-interval',type=int)
 ap.add_argument('--pingbo-interval',type=int); ap.add_argument('--fb-max-events',type=int,default=8); ap.add_argument('--eb-max-events',type=int,default=50)
 ap.add_argument('--timeout',type=int,default=15); ap.add_argument('--max-runtime',type=int,default=0)
 a=ap.parse_args()
 if a.setup_session:
  setup_session(a.setup_session,a.profiles_dir); return
 policy=json.loads(Path(a.policy).read_text(encoding='utf-8'))
 intervals=policy.get('intervals_seconds',{})
 a.fb_list_interval=a.fb_list_interval or intervals.get('FB_event_list',30)
 a.fb_odds_interval=intervals.get('FB_single_event_odds',20)
 a.eb_list_interval=a.eb_list_interval or intervals.get('EB_event_list',5)
 # EB odds and Pingbo event+embedded-odds refresh share one policy interval.
 eb_pingbo_interval=intervals.get('EB_Pingbo_shared_interval',20)
 a.eb_odds_interval=eb_pingbo_interval
 a.pingbo_interval=eb_pingbo_interval
 vals=[a.fb_list_interval,a.eb_list_interval,a.timeout]
 if min(vals)<5: ap.error('赛事列表刷新间隔及超时不得低于5秒')
 cfg=json.loads(Path(a.config).read_text(encoding='utf-8'))
 op=cfg.get('op7',{}); pb=cfg.get('part567',{})
 fb_body=op.get('fb_list_body',{'size':50,'sportId':1,'type':1,'orderBy':1,'current':1,'isPC':True,'languageType':'CMN'})
 fb_detail=op.get('fb_detail_template',{'oddsType':1,'languageType':'CMN'})
 eb_body=op.get('list_body',{}); eb_q=op.get('odds_params',{})
 pb_q=pb.get('events_params',{})
 if not all(isinstance(x,dict) for x in (fb_body,fb_detail,eb_body,eb_q,pb_q)): raise ValueError('All request parameter blocks must be JSON objects')
 if not a.live:
  print(json.dumps({'mode':'DRY_RUN_NO_NETWORK','endpoints':{'FB':'getList POST + getMatchDetail POST','EB':'matchesPagePB POST + getMatchOddsInfoPB GET','Pingbo':'compact/events GET'},
   'refresh_seconds':{'FB_list':a.fb_list_interval,'FB_odds':20,'EB_list':a.eb_list_interval,'EB_odds':20,'Pingbo_events_and_embedded_odds':20},
   'max_events':{'FB':a.fb_max_events,'EB':a.eb_max_events},'bet_or_order_endpoints':'NOT ALLOWED'},ensure_ascii=False,indent=2)); return
 profiles=Path(a.profiles_dir); (profiles/'op7').mkdir(parents=True,exist_ok=True); (profiles/'pingbo').mkdir(parents=True,exist_ok=True)
 out=Path(a.out); oh=headers_env('OP7_READONLY_HEADERS_JSON'); ph=headers_env('PINGBO_READONLY_HEADERS_JSON')
 global PINGBO_ANALYSIS_DIR,PINGBO_ANALYSIS_FILE
 PINGBO_ANALYSIS_DIR=Path(os.environ.get('PINGBO_API_ANALYSIS_DIR',str(out/'pingbo_api_analysis')))
 PINGBO_ANALYSIS_DIR.mkdir(parents=True,exist_ok=True)
 stamp=datetime.now().strftime('%Y%m%d_%H%M%S')+f'_{time.time_ns()%1_000_000_000:09d}'
 default_file=PINGBO_ANALYSIS_DIR/f'pingbo_api_trace_{stamp}.jsonl'
 PINGBO_ANALYSIS_FILE=Path(os.environ.get('PINGBO_API_ANALYSIS_FILE',str(default_file)))
 PINGBO_ANALYSIS_FILE.parent.mkdir(parents=True,exist_ok=True)
 PINGBO_ANALYSIS_FILE.touch(exist_ok=False) if not PINGBO_ANALYSIS_FILE.exists() else None
 print(f'[Pingbo分析] 页面自动请求、脚本请求/响应及页面可见变化将记录到：{PINGBO_ANALYSIS_FILE.resolve()}')
 with sync_playwright() as p:
  opctx=p.chromium.launch_persistent_context(str(profiles/'op7'),headless=False,args=['--no-sandbox','--disable-dev-shm-usage'])
  pbctx=p.chromium.launch_persistent_context(str(profiles/'pingbo'),headless=False,args=['--no-sandbox','--disable-dev-shm-usage'])
  try:
   for ctx,url,label,family in ((opctx,SITES['OP7_WEB'],'OP7','op7'),(pbctx,SITES['PINGBO_WEB'],'Pingbo','pingbo')):
    page=ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on('request',lambda req,f=family: capture_request(req,f))
    if family=='pingbo':
     page.on('response',lambda resp,pg=page: _pingbo_page_response(resp,pg))
    try:
     if page.url=='about:blank': page.goto(url,wait_until='domcontentloaded',timeout=a.timeout*1000)
     else: page.reload(wait_until='domcontentloaded',timeout=a.timeout*1000)
    except PlaywrightTimeoutError: print(f'{label} page warm-up timeout; continue with existing session',file=sys.stderr)
   # Prefer API origins observed from real requests emitted by the logged-in tabs.
   if 'fb' in CAPTURED_ORIGINS: SITES['FB_ORIGIN']=CAPTURED_ORIGINS['fb']
   if 'eb' in CAPTURED_ORIGINS: SITES['EB_ORIGIN']=CAPTURED_ORIGINS['eb']
   if 'pingbo' in CAPTURED_ORIGINS: SITES['PINGBO_ORIGIN']=CAPTURED_ORIGINS['pingbo']
   fb_list=[]; eb_list=[]; pb_list=[]; market_cache={'FB':{},'EB':{},'PINGBO':{}}; clocks={'fb_list':0,'fb_odds':0,'eb_list':0,'eb_odds':0,'pb':0}; started=time.monotonic()
   print('只读实时刷新已启动；不会调用投注、订单、钱包或资金接口。Ctrl+C停止。')
   while True:
    auth_failure=None
    tick=time.monotonic()
    if a.max_runtime and tick-started>=a.max_runtime: break
    # FB list
    if tick-clocks['fb_list']>=a.fb_list_interval or not fb_list:
     try:
      res=request_json(opctx,SITES['FB_ORIGIN']+'/v1/match/getList','POST',oh,body=fb_body,timeout_ms=a.timeout*1000,family='fb')
      fb_list=fb_events(res); save_latest(out,'FB_events',fb_list); clocks['fb_list']=time.monotonic()
      print(f'[{now()}] FB赛事列表刷新：{len(fb_list)}场')
     except Exception as e:
      save_failure(out,'FB_events',e); print(f'FB列表刷新失败：{type(e).__name__}: {e}',file=sys.stderr)
      if isinstance(e,AuthenticationRequired): auth_failure=e
    if auth_failure: report_auth_failure(auth_failure,a); break
    # EB list
    if tick-clocks['eb_list']>=a.eb_list_interval or not eb_list:
     try:
      res=request_json(opctx,SITES['EB_ORIGIN']+'/yewu11/v1/m/matchesPagePB','POST',oh,body=eb_body,timeout_ms=a.timeout*1000,family='eb')
      eb_rows=eb_events(res)
      eb_list=[x for x in eb_rows if mapper.is_nba_league(x.get('league'))]
      save_latest(out,'EB_events',eb_list); clocks['eb_list']=time.monotonic()
      print(f'[{now()}] EB赛事列表刷新：{len(eb_list)}场')
     except Exception as e:
      save_failure(out,'EB_events',e); print(f'EB列表刷新失败：{type(e).__name__}: {e}',file=sys.stderr)
      if isinstance(e,AuthenticationRequired): auth_failure=e
    if auth_failure: report_auth_failure(auth_failure,a); break
    # Pingbo list + embedded markets
    if tick-clocks['pb']>=a.pingbo_interval or not pb_list:
     try:
      pb_params=dict(pb_q); pb_params.setdefault('_',str(int(time.time()*1000)))
      res=request_json(pbctx,SITES['PINGBO_ORIGIN']+'/sports-service/sv/compact/events','GET',ph,params=pb_params,timeout_ms=a.timeout*1000,family='pingbo')
      fetched_at=now()
      pb_rows=mapper.extract_pingbo_events(res)
      # Unknown league labels are discarded too: only explicitly confirmed NBA
      # events and their embedded markets may enter the monitoring pipeline.
      pb_list=[event for event in pb_rows if mapper.is_nba_league(event.get('league'))]
      for event in pb_list: event['observed_at_utc']=fetched_at
      normalized=[{k:v for k,v in x.items() if not k.startswith('_')} for x in pb_list]
      pb_markets=[r for x in pb_list for r in mapper.flatten_pingbo_markets(x)]
      pb_markets=update_market_cache(market_cache['PINGBO'],'PINGBO',pb_markets,[x['event_id'] for x in pb_list],fetched_at)
      save_latest(out,'Pingbo_events',{'events':normalized,'markets':pb_markets}); clocks['pb']=time.monotonic()
      print(f'[{now()}] Pingbo赛事+内嵌盘口刷新：{len(pb_list)}场 / {len(pb_markets)}条当前盘口记录')
     except Exception as e:
      save_failure(out,'Pingbo_events',e); print(f'Pingbo刷新失败：{type(e).__name__}: {e}',file=sys.stderr)
      if isinstance(e,AuthenticationRequired): auth_failure=e
    if auth_failure: report_auth_failure(auth_failure,a); break
    # FB event details / odds
    if tick-clocks['fb_odds']>=a.fb_odds_interval:
     rows=[]; failed_events=0
     for ev in fb_list[:max(0,a.fb_max_events)]:
      try:
       body=dict(fb_detail); body['matchId']=ev['id']
       res=request_json(opctx,SITES['FB_ORIGIN']+'/v1/match/getMatchDetail','POST',oh,body=body,timeout_ms=a.timeout*1000,family='fb')
       d=data_of(res); markets,bad=mapper.flatten_fb_markets(d,2,ev['id'])
       for row in markets: row['observed_at_utc']=now()
       rows.extend(markets)
      except Exception as e:
       failed_events+=1; print(f"FB赛事{ev.get('id')}盘口刷新失败：{type(e).__name__}",file=sys.stderr)
       if isinstance(e,AuthenticationRequired): auth_failure=e; break
     if auth_failure:
      save_failure(out,'FB_markets',auth_failure)
     else:
      rows=update_market_cache(market_cache['FB'],'FB',rows,[x['id'] for x in fb_list],now())
      save_latest(out,'FB_markets',rows,'PARTIAL' if failed_events else 'SUCCESS',{'failed_events':failed_events})
     clocks['fb_odds']=time.monotonic()
     print(f'[{now()}] FB盘口刷新：{len(rows)}组盘口')
    if auth_failure: report_auth_failure(auth_failure,a); break
    # EB event odds
    if tick-clocks['eb_odds']>=a.eb_odds_interval:
     rows=[]; failed_events=0
     for ev in eb_list[:max(0,a.eb_max_events)]:
      try:
       q=dict(eb_q); q['mid']=ev['id']
       res=request_json(opctx,SITES['EB_ORIGIN']+'/yewu11/v1/m/matchDetail/getMatchOddsInfoPB','GET',oh,params=q,timeout_ms=a.timeout*1000,family='eb')
       d=data_of(res); markets,bad=mapper.flatten_eb_markets(d,2,{})
       rows.extend(markets)
      except Exception as e:
       failed_events+=1; print(f"EB赛事{ev.get('id')}盘口刷新失败：{type(e).__name__}",file=sys.stderr)
       if isinstance(e,AuthenticationRequired): auth_failure=e; break
     observed_at=now()
     for row in rows: row['observed_at_utc']=observed_at
     if auth_failure:
      save_failure(out,'EB_markets',auth_failure)
     else:
      rows=update_market_cache(market_cache['EB'],'EB',rows,[x['id'] for x in eb_list],observed_at)
      save_latest(out,'EB_markets',rows,'PARTIAL' if failed_events else 'SUCCESS',{'failed_events':failed_events})
     clocks['eb_odds']=time.monotonic()
     print(f'[{now()}] EB盘口刷新：{len(rows)}组盘口')
    if auth_failure: report_auth_failure(auth_failure,a); break
    _capture_pingbo_ui_snapshot(pbctx.pages[0] if pbctx.pages else None) if pbctx.pages else None
    time.sleep(0.5)
  finally:
   opctx.close(); pbctx.close()

if __name__=='__main__':
 try: main()
 except KeyboardInterrupt: print('\n已停止。')
