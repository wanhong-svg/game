#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OP7 FB/EB + Pingbo live read-only refresher.

Only allow-listed public event-list / event-detail / odds-read endpoints are called.
No bet, order, wallet, deposit, withdrawal, cashout, or account mutation endpoints.
Uses normal Playwright persistent browser profiles; no CAPTCHA/MFA bypass.
"""
import argparse, base64, gzip, json, os, sys, time, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import op7_fb_eb_venue_mapper as mapper

SITES={
 'OP7_WEB':'https://op7.io/zh/',
 'FB_ORIGIN':'https://api.738z3.com',
 'EB_ORIGIN':'https://api.r4bud5u7.com',
 'PINGBO_WEB':'https://www.pin135.com/',
 'PINGBO_ORIGIN':'https://www.part567.com',
}
ALLOW={
 'api.738z3.com':{'/v1/match/getList','/v1/match/getMatchDetail'},
 'api.r4bud5u7.com':{'/yewu11/v1/m/matchesPagePB','/yewu11/v1/m/matchDetail/getMatchOddsInfoPB'},
 'www.part567.com':{'/sports-service/sv/compact/events'},
}

def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds')
def headers_env(name):
 raw=os.environ.get(name,'')
 if not raw: return {'Accept':'application/json','User-Agent':'Mozilla/5.0 ReadOnlyMonitor/1.0'}
 h=json.loads(raw)
 if not isinstance(h,dict): raise ValueError(f'{name} must be a JSON object')
 return {k:v for k,v in h.items() if isinstance(k,str) and isinstance(v,str) and k.lower() not in {'cookie','host','content-length','connection','transfer-encoding'}}

def unwrap(obj):
 if isinstance(obj,dict) and isinstance(obj.get('data'),str):
  try:
   b=base64.b64decode(obj['data'])
   for _ in range(3):
    try: b=gzip.decompress(b)
    except Exception: break
   z=json.loads(b.decode('utf-8','replace'))
   obj=dict(obj); obj['data']=z
  except Exception: pass
 return obj

class AuthenticationRequired(RuntimeError):
 def __init__(self, site, status, url):
  self.site=site; self.status=status; self.url=url
  super().__init__(f'{site} HTTP {status}: 登录会话失效或无权访问；请使用 --setup-session {site} 重新登录并检查账号权限')

def request_json(ctx,url,method,headers,params=None,body=None,timeout_ms=15000):
 p=urllib.parse.urlsplit(url)
 if p.scheme!='https' or p.netloc not in ALLOW or p.path not in ALLOW[p.netloc] or method not in ('GET','POST'):
  raise ValueError('Blocked non-allow-listed destination or method')
 if params:
  q=urllib.parse.urlencode(params,doseq=True); url+=('&' if '?' in url else '?')+q
 if method=='GET': r=ctx.request.get(url,headers=headers,timeout=timeout_ms)
 else: r=ctx.request.post(url,headers=headers,data=body or {},timeout=timeout_ms)
 if r.status in (401,403):
  site='pingbo' if p.netloc=='www.part567.com' else 'op7'
  raise AuthenticationRequired(site,r.status,f'{p.netloc}{p.path}')
 if not r.ok: raise RuntimeError(f'HTTP {r.status} from {p.netloc}{p.path}')
 return unwrap(r.json())

def data_of(obj):
 return obj.get('data') if isinstance(obj,dict) and 'data' in obj else obj

def fb_events(payload):
 d=data_of(payload); rows=d.get('records',[]) if isinstance(d,dict) else []
 out=[]
 for x in rows if isinstance(rows,list) else []:
  if not isinstance(x,dict): continue
  nm=str(x.get('nm') or '')
  parts=__import__('re').split(r'\s+vs\.?\s+',nm,maxsplit=1,flags=__import__('re').I)
  if len(parts)==2:
   out.append({'id':str(x.get('id')),'home':parts[0].strip(),'away':parts[1].strip(),'name':nm,'raw_status_ms':x.get('ms'),'phase_pe':(x.get('mc') or {}).get('pe') if isinstance(x.get('mc'),dict) else None})
 return out

def eb_events(payload):
 d=data_of(payload)
 if isinstance(d,dict): d=d.get('data',[])
 out=[]
 for x in d if isinstance(d,list) else []:
  if not isinstance(x,dict): continue
  mid=x.get('mid') or x.get('id'); home=x.get('mhn') or x.get('homeName'); away=x.get('man') or x.get('awayName')
  if mid and home and away: out.append({'id':str(mid),'home':str(home),'away':str(away),'raw_status':x.get('mst'),'phase_mmp':x.get('mmp')})
 return out

def save_latest(out,name,payload,status='SUCCESS',details=None):
 out.mkdir(parents=True,exist_ok=True)
 stamp=now()
 p=out/(name+'_latest.json')
 tmp=p.with_suffix('.tmp')
 tmp.write_text(json.dumps({'status':status,'fetched_at_utc':stamp,'data':payload,'details':details or {}},ensure_ascii=False,indent=2),encoding='utf-8')
 tmp.replace(p)
 (out/(name+'_status.json')).write_text(json.dumps({'status':status,'observed_at_utc':stamp,'details':details or {},'error':None},ensure_ascii=False,indent=2),encoding='utf-8')

def save_failure(out,name,error):
 out.mkdir(parents=True,exist_ok=True)
 status='AUTH_REQUIRED' if isinstance(error,AuthenticationRequired) else 'FAILED'
 (out/(name+'_status.json')).write_text(json.dumps({'status':status,'failed_at_utc':now(),'error_type':type(error).__name__,'error':str(error)},ensure_ascii=False,indent=2),encoding='utf-8')

def setup_session(site,profiles_dir):
 sites={'op7':('https://op7.io/zh/','OP7'),'pingbo':('https://www.pin135.com/','Pingbo')}
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
    relevant=[d for d in domains if ('op7.io' in d or '738z3.com' in d or 'r4bud5u7.com' in d)] if key=='op7' else [d for d in domains if ('pin135.com' in d or 'part567.com' in d)]
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
 ap.add_argument('--pingbo-interval',type=int); ap.add_argument('--fb-max-events',type=int,default=8); ap.add_argument('--eb-max-events',type=int,default=10)
 ap.add_argument('--timeout',type=int,default=15); ap.add_argument('--max-runtime',type=int,default=0)
 a=ap.parse_args()
 if a.setup_session:
  setup_session(a.setup_session,a.profiles_dir); return
 policy=json.loads(Path(a.policy).read_text(encoding='utf-8'))
 intervals=policy.get('intervals_seconds',{})
 a.fb_list_interval=a.fb_list_interval or intervals.get('FB_event_list',30)
 a.fb_odds_interval=a.fb_odds_interval or intervals.get('FB_single_event_odds',6)
 a.eb_list_interval=a.eb_list_interval or intervals.get('EB_event_list',5)
 a.eb_odds_interval=a.eb_odds_interval or intervals.get('EB_single_event_odds',5)
 a.pingbo_interval=a.pingbo_interval or intervals.get('Pingbo_event_list_and_embedded_odds',20)
 vals=[a.fb_list_interval,a.fb_odds_interval,a.eb_list_interval,a.eb_odds_interval,a.pingbo_interval,a.timeout]
 if min(vals)<5: ap.error('刷新间隔及超时不得低于5秒')
 cfg=json.loads(Path(a.config).read_text(encoding='utf-8'))
 op=cfg.get('op7',{}); pb=cfg.get('part567',{})
 fb_body=op.get('fb_list_body',{'size':50,'sportId':1,'type':1,'orderBy':1,'current':1,'isPC':True,'languageType':'CMN'})
 fb_detail=op.get('fb_detail_template',{'oddsType':1,'languageType':'CMN'})
 eb_body=op.get('list_body',{}); eb_q=op.get('odds_params',{})
 pb_q=pb.get('events_params',{})
 if not all(isinstance(x,dict) for x in (fb_body,fb_detail,eb_body,eb_q,pb_q)): raise ValueError('All request parameter blocks must be JSON objects')
 if not a.live:
  print(json.dumps({'mode':'DRY_RUN_NO_NETWORK','endpoints':{'FB':'getList POST + getMatchDetail POST','EB':'matchesPagePB POST + getMatchOddsInfoPB GET','Pingbo':'compact/events GET'},
   'refresh_seconds':{'FB_list':a.fb_list_interval,'FB_odds':a.fb_odds_interval,'EB_list':a.eb_list_interval,'EB_odds':a.eb_odds_interval,'Pingbo_events_and_embedded_odds':a.pingbo_interval},
   'max_events':{'FB':a.fb_max_events,'EB':a.eb_max_events},'bet_or_order_endpoints':'NOT ALLOWED'},ensure_ascii=False,indent=2)); return
 profiles=Path(a.profiles_dir); (profiles/'op7').mkdir(parents=True,exist_ok=True); (profiles/'pingbo').mkdir(parents=True,exist_ok=True)
 out=Path(a.out); oh=headers_env('OP7_READONLY_HEADERS_JSON'); ph=headers_env('PINGBO_READONLY_HEADERS_JSON')
 with sync_playwright() as p:
  opctx=p.chromium.launch_persistent_context(str(profiles/'op7'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  pbctx=p.chromium.launch_persistent_context(str(profiles/'pingbo'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  try:
   for ctx,url,label in ((opctx,SITES['OP7_WEB'],'OP7'),(pbctx,SITES['PINGBO_WEB'],'Pingbo')):
    page=ctx.pages[0] if ctx.pages else ctx.new_page()
    try: page.goto(url,wait_until='domcontentloaded',timeout=a.timeout*1000)
    except PlaywrightTimeoutError: print(f'{label} page warm-up timeout; continue with existing session',file=sys.stderr)
   fb_list=[]; eb_list=[]; pb_list=[]; clocks={'fb_list':0,'fb_odds':0,'eb_list':0,'eb_odds':0,'pb':0}; started=time.monotonic()
   print('只读实时刷新已启动；不会调用投注、订单、钱包或资金接口。Ctrl+C停止。')
   while True:
    auth_failure=None
    tick=time.monotonic()
    if a.max_runtime and tick-started>=a.max_runtime: break
    # FB list
    if tick-clocks['fb_list']>=a.fb_list_interval or not fb_list:
     try:
      res=request_json(opctx,SITES['FB_ORIGIN']+'/v1/match/getList','POST',oh,body=fb_body,timeout_ms=a.timeout*1000)
      fb_list=fb_events(res); save_latest(out,'FB_events',fb_list); clocks['fb_list']=time.monotonic()
      print(f'[{now()}] FB赛事列表刷新：{len(fb_list)}场')
     except Exception as e:
      save_failure(out,'FB_events',e); print(f'FB列表刷新失败：{type(e).__name__}: {e}',file=sys.stderr)
      if isinstance(e,AuthenticationRequired): auth_failure=e
    if auth_failure: report_auth_failure(auth_failure,a); break
    # EB list
    if tick-clocks['eb_list']>=a.eb_list_interval or not eb_list:
     try:
      res=request_json(opctx,SITES['EB_ORIGIN']+'/yewu11/v1/m/matchesPagePB','POST',oh,body=eb_body,timeout_ms=a.timeout*1000)
      eb_list=eb_events(res); save_latest(out,'EB_events',eb_list); clocks['eb_list']=time.monotonic()
      print(f'[{now()}] EB赛事列表刷新：{len(eb_list)}场')
     except Exception as e:
      save_failure(out,'EB_events',e); print(f'EB列表刷新失败：{type(e).__name__}: {e}',file=sys.stderr)
      if isinstance(e,AuthenticationRequired): auth_failure=e
    if auth_failure: report_auth_failure(auth_failure,a); break
    # Pingbo list + embedded markets
    if tick-clocks['pb']>=a.pingbo_interval or not pb_list:
     try:
      pb_params=dict(pb_q); pb_params.setdefault('_',str(int(time.time()*1000)))
      res=request_json(pbctx,SITES['PINGBO_ORIGIN']+'/sports-service/sv/compact/events','GET',ph,params=pb_params,timeout_ms=a.timeout*1000)
      pb_list=mapper.extract_pingbo_events(res)
      normalized=[{k:v for k,v in x.items() if not k.startswith('_')} for x in pb_list]
      pb_markets=[r for x in pb_list for r in mapper.flatten_pingbo_markets(x)]
      save_latest(out,'Pingbo_events',{'events':normalized,'markets':pb_markets}); clocks['pb']=time.monotonic()
      print(f'[{now()}] Pingbo赛事+内嵌盘口刷新：{len(pb_list)}场 / {len(pb_markets)}条盘口选项')
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
       res=request_json(opctx,SITES['FB_ORIGIN']+'/v1/match/getMatchDetail','POST',oh,body=body,timeout_ms=a.timeout*1000)
       d=data_of(res); markets,bad=mapper.flatten_fb_markets(d,2,ev['id'])
       for row in markets: row['observed_at_utc']=now()
       rows.extend(markets)
      except Exception as e:
       failed_events+=1; print(f"FB赛事{ev.get('id')}盘口刷新失败：{type(e).__name__}",file=sys.stderr)
       if isinstance(e,AuthenticationRequired): auth_failure=e; break
     save_latest(out,'FB_markets',rows,'PARTIAL' if failed_events else 'SUCCESS',{'failed_events':failed_events}); clocks['fb_odds']=time.monotonic()
     print(f'[{now()}] FB盘口刷新：{len(rows)}组盘口')
    if auth_failure: report_auth_failure(auth_failure,a); break
    # EB event odds
    if tick-clocks['eb_odds']>=a.eb_odds_interval:
     rows=[]; failed_events=0
     for ev in eb_list[:max(0,a.eb_max_events)]:
      try:
       q=dict(eb_q); q['mid']=ev['id']
       res=request_json(opctx,SITES['EB_ORIGIN']+'/yewu11/v1/m/matchDetail/getMatchOddsInfoPB','GET',oh,params=q,timeout_ms=a.timeout*1000)
       d=data_of(res); markets,bad=mapper.flatten_eb_markets(d,2,{})
       rows.extend(markets)
      except Exception as e:
       failed_events+=1; print(f"EB赛事{ev.get('id')}盘口刷新失败：{type(e).__name__}",file=sys.stderr)
       if isinstance(e,AuthenticationRequired): auth_failure=e; break
     for row in rows: row['observed_at_utc']=now()
     save_latest(out,'EB_markets',rows,'PARTIAL' if failed_events else 'SUCCESS',{'failed_events':failed_events}); clocks['eb_odds']=time.monotonic()
     print(f'[{now()}] EB盘口刷新：{len(rows)}组盘口')
    if auth_failure: report_auth_failure(auth_failure,a); break
    time.sleep(0.5)
  finally:
   opctx.close(); pbctx.close()

if __name__=='__main__':
 try: main()
 except KeyboardInterrupt: print('\n已停止。')
