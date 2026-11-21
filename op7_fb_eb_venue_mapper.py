#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline OP7 FB/EB + Part567(Pingbo) HAR analyzer.

Reads HAR files only. No HTTP requests, authentication replay, bet submission,
order mutation, wallet mutation, cashout, deposit or withdrawal actions.
"""
import argparse, base64, gzip, json, re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

FB_HOST='api.738z3.com'; EB_HOST='api.r4bud5u7.com'; COMMON_HOST='api.live336.com'; PB_HOST='www.part567.com'
FB_MATCH_STATUS={0:'已结束',1:'推迟（较长）',2:'中断',3:'取消',4:'未开赛',5:'进行中',6:'延迟（较短）',7:'废弃',8:'暂停'}
FB_PHASE={1001:'即将开始',1002:'上半场',1003:'中场休息',1004:'下半场',1005:'全场结束',1006:'加时上半场',1008:'加时下半场',1011:'全场结束',1014:'点球结束',1015:'中断',1016:'废弃/放弃'}
EB_PHASE={0:'即将开始',6:'上半场',31:'中场休息',302:'半场',7:'下半场',41:'加时上半场',42:'加时下半场',80:'中断',90:'废弃/放弃',100:'全场结束',999:'全场结束',120:'点球结束'}
EB_FULLTIME={100,990,999,120}
PLAY_MAP=[
 {'sport':'足球','period':'全场','market':'让球','FB_mty':1000,'FB_pe':1001,'EB_playId':4},
 {'sport':'足球','period':'全场','market':'大小','FB_mty':1007,'FB_pe':1001,'EB_playId':2},
 {'sport':'足球','period':'全场','market':'独赢','FB_mty':1005,'FB_pe':1001,'EB_playId':1},
 {'sport':'足球','period':'上半场','market':'让球','FB_mty':1000,'FB_pe':1002,'EB_playId':19},
 {'sport':'足球','period':'上半场','market':'大小','FB_mty':1007,'FB_pe':1002,'EB_playId':18},
 {'sport':'足球','period':'上半场','market':'独赢','FB_mty':1005,'FB_pe':1002,'EB_playId':17},
 {'sport':'篮球','period':'全场（FB配置含加时）','market':'让分','FB_mty':3002,'FB_pe':3001,'EB_playId':39},
 {'sport':'篮球','period':'全场（FB配置含加时）','market':'大小','FB_mty':3003,'FB_pe':3001,'EB_playId':38},
 {'sport':'篮球','period':'全场（FB配置含加时）','market':'独赢','FB_mty':3004,'FB_pe':3001,'EB_playId':37},
]

def decode_response(entry):
    c=entry.get('response',{}).get('content',{}); raw=c.get('text')
    if not raw: return None
    try: b=base64.b64decode(raw) if c.get('encoding')=='base64' else raw.encode()
    except Exception: return None
    for _ in range(3):
        try: b=gzip.decompress(b)
        except Exception: break
    try: obj=json.loads(b.decode('utf-8','replace'))
    except Exception: return None
    if isinstance(obj,dict) and isinstance(obj.get('data'),str):
        try:
            inner=base64.b64decode(obj['data'])
            for _ in range(3):
                try: inner=gzip.decompress(inner)
                except Exception: break
            obj=dict(obj); obj['data']=json.loads(inner.decode('utf-8','replace'))
        except Exception: pass
    return obj

def identify_venue(url):
    p=urlsplit(url); h=p.netloc.lower(); path=p.path; q=parse_qs(p.query)
    if h==FB_HOST and path.startswith('/v1/match/'): return 'FB'
    if h==EB_HOST and path.startswith('/yewu11/v1/') and any(x in path for x in ('matchesPagePB','getMatchOddsInfoPB','getMatchDetailPB','getMatchBaseInfoByMidsPB','esportsMatchesPB','noLiveMatchesPagePB','getFilterMatchListPB')): return 'EB'
    if h==COMMON_HOST:
        gt=(q.get('gameType') or [''])[0].upper()
        if gt=='FB': return 'FB'
        if gt=='OB': return 'EB'
    if h==PB_HOST and path.startswith('/sports-service/sv/compact/'): return 'PINGBO'
    return None

def walk(x):
    if isinstance(x,dict):
        yield x
        for v in x.values():
            if isinstance(v,(dict,list)): yield from walk(v)
    elif isinstance(x,list):
        for v in x: yield from walk(v)

def first(d,*keys):
    for k in keys:
        v=d.get(k)
        if v not in (None,''): return v
    return None

def as_int(v):
    try: return int(v)
    except (ValueError,TypeError): return None

def dec(v):
    try: return Decimal(str(v))
    except (InvalidOperation,ValueError,TypeError): return None

def trunc(v,places=2):
    if v is None: return None
    try: return str(Decimal(str(v)).quantize(Decimal('1').scaleb(-places),rounding=ROUND_DOWN))
    except Exception: return None

def fb_sales_status(ss): return {1:1,0:2,-1:4}.get(as_int(ss),4)

def eb_market_status(mhs=None,hs=None,os=None,mo=None):
    # Common status: 1=open, 2=suspended/locked, 4=closed.
    vals=[as_int(mhs),as_int(hs)]; osn=as_int(os); mon=as_int(mo)
    if mon==1 or any(v in (1,2,11) for v in vals if v is not None) or osn==2: return 2
    if any(v==0 for v in vals if v is not None) and osn in (None,1): return 1
    return 4

def event_summary(d,venue):
    if venue=='EB':
        phase=first(d,'mmp')
        return {'venue':'EB','event_id':first(d,'mid','id'),'league':first(d,'tnjc','leagueName'),
          'sport':first(d,'csna','sportName'),'home':first(d,'mhn','homeName','homeTeamName'),
          'away':first(d,'man','awayName','awayTeamName'),'start_time_ms':first(d,'mgt','startTime'),
          'raw_match_status':first(d,'mst','mstst','mststs','msts','mss'),
          'phase_code_mmp':phase,'phase_text':EB_PHASE.get(as_int(phase),f'未知阶段({phase})' if phase is not None else None),
          'is_fulltime_by_mmp':as_int(phase) in EB_FULLTIME if phase is not None else None,'live_status':first(d,'lvs'),
          'market_group_count':len(d.get('hps',[]) or []),'raw_fields':sorted(d.keys())}
    mc=d.get('mc') if isinstance(d.get('mc'),dict) else {}; phase=first(mc,'pe'); ms=first(d,'ms')
    lg=d.get('lg') if isinstance(d.get('lg'),dict) else {}; nm=str(first(d,'nm') or '')
    teams=re.split(r'\s+vs\.?\s+',nm,maxsplit=1,flags=re.I)
    parsed_home,parsed_away=(teams[0].strip(),teams[1].strip()) if len(teams)==2 else (None,None)
    # Prefer explicit source fields, then fall back to the event-name split.
    home=first(d,'home','hn','homeName') or parsed_home
    away=first(d,'away','an','awayName') or parsed_away
    return {'venue':'FB','event_id':first(d,'id','mid'),'league':first(lg,'na','nm','name'),'event_name':nm,
      'home':home,'away':away,'sport_type':first(d,'ty'),'start_time_ms':first(d,'bt','matchTime','startTime'),
      'match_clock_or_elapsed':first(d,'tms'),'raw_match_status_ms':ms,
      'match_status_text':FB_MATCH_STATUS.get(as_int(ms),f'未知({ms})'),
      'phase_code_mc_pe':phase,'phase_text':FB_PHASE.get(as_int(phase),f'未知阶段({phase})' if phase is not None else None),
      'market_group_count':len(d.get('mg',[]) or []),'raw_fields':sorted(d.keys())}

def flatten_fb_markets(root,precision=2,event_id=None):
    out=[]; invalid=0
    event_id=event_id or (first(root,'id','mid') if isinstance(root,dict) else None)
    for node in walk(root):
        mg=node.get('mg') if isinstance(node,dict) else None
        if not isinstance(mg,list): continue
        eid=first(node,'id','mid') or event_id
        for market in mg:
            if not isinstance(market,dict): continue
            for group in market.get('mks',[]) or []:
                if not isinstance(group,dict): continue
                ss=group.get('ss'); common=fb_sales_status(ss); sels=[]
                for op in group.get('op',[]) or []:
                    if not isinstance(op,dict): continue
                    odds=dec(op.get('od'))
                    valid=odds is not None and odds>Decimal('1')
                    if not valid: invalid+=1; continue
                    sels.append({'selection_id':first(op,'id','oid'),'selection':first(op,'na','nm'),
                      'raw_odds':op.get('od'),'base_odds':op.get('bod'),'decimal_odds':str(odds),
                      'display_europe':trunc(odds,precision),'common_market_status':common,'selection_type':op.get('ty'),'odds_type':op.get('odt')})
                out.append({'venue':'FB','event_id':eid,'market_id':first(market,'id','gid'),'market_name':first(market,'na','nm'),
                  'market_type_mty':first(market,'mty','ty'),'settlement_period_pe':market.get('pe'),
                  'group_id':first(group,'id','mid'),'line':first(group,'li','nm'),'raw_sales_status_ss':ss,
                  'common_market_status':common,'selections':sels,'invalid_selection_count':sum(1 for op in (group.get('op',[]) or []) if isinstance(op,dict) and not ((dec(op.get('od')) or Decimal('0'))>1))})
    return out,invalid

def flatten_eb_markets(data,precision=2,event_status_by_mid=None):
    out=[]; invalid=0; event_status_by_mid=event_status_by_mid or {}
    for market in data if isinstance(data,list) else []:
        if not isinstance(market,dict): continue
        mid=first(market,'mid','matchId'); ev=event_status_by_mid.get(str(mid),{})
        mhs=first(market,'mhs') if first(market,'mhs') is not None else ev.get('mhs'); mo=first(market,'mo') if first(market,'mo') is not None else ev.get('mo')
        for group in market.get('hl',[]) or []:
            if not isinstance(group,dict): continue
            sels=[]
            for opt in group.get('ol',[]) or []:
                if not isinstance(opt,dict): continue
                raw=opt.get('ov'); odds=(dec(raw)/Decimal(100000)) if dec(raw) is not None else None
                if odds is None or odds<=1: invalid+=1; continue
                os=opt.get('os'); common=eb_market_status(mhs,group.get('hs'),os,mo)
                sels.append({'selection_id':opt.get('oid'),'selection_code':opt.get('ot'),
                  'selection':first(opt,'otv','ott','onb','on'),'raw_odds_ov':raw,'decimal_odds':str(odds),
                  'display_europe':trunc(odds,precision),'selection_status_os':os,'common_market_status':common,
                  'selection_type_id':opt.get('otd'),'source_code':opt.get('cds'),'side_code':opt.get('ots')})
            out.append({'venue':'EB','event_id':mid,'market_id':first(market,'hid','hlid'),'market_name':market.get('hpn'),
              'play_id_hpid':market.get('hpid'),'display_type_hpt':market.get('hpt'),'market_enabled_hpon':market.get('hpon'),
              'market_show_hshow':market.get('hshow'),'market_switch_hsw':market.get('hsw'),'group_id':group.get('hid'),
              'line_hv':group.get('hv'),'group_status_hs':group.get('hs'),'raw_event_status_mo':mo,'selections':sels})
    return out,invalid

def norm_team(s): return re.sub(r'[\s\u3000\-_.()（）\[\]【】]+','',str(s or '').strip().lower())

def extract_pingbo_events(payload):
    found=[]
    def rec(x):
        if isinstance(x,list):
            if len(x)>=9 and isinstance(x[1],str) and isinstance(x[2],str) and isinstance(x[8],dict):
                found.append({'venue':'PINGBO','event_id':str(x[0]),'home':x[1],'away':x[2],
                  'raw_status':x[3] if len(x)>3 else None,'start_time_ms':x[4] if len(x)>4 else None,
                  'market_keys':sorted(x[8].keys()),'raw_tuple_length':len(x),'_raw_markets':x[8]})
            else:
                for z in x: rec(z)
        elif isinstance(x,dict):
            for z in x.values(): rec(z)
    rec(payload)
    unique={e['event_id']:e for e in found}
    return list(unique.values())

def flatten_pingbo_markets(event):
    rows=[]; markets=event.get('_raw_markets',{})
    # Captured compact/events tuple: key 0 is handicap array; key 1 is totals array.
    # Keep this as source-key mapping; no unsupported equivalence to FB/EB is inferred.
    for r in markets.get('0',[]) if isinstance(markets,dict) else []:
        if not isinstance(r,list) or len(r)<5: continue
        for side,idx,line_idx,odds_idx in [('home',0,2,3),('away',1,2,4)]:
            odds=dec(r[odds_idx]);
            if odds is None or odds<=1: continue
            rows.append({'venue':'PINGBO','event_id':event['event_id'],'market_key':'0','market_label':'抓包结构key=0（历史脚本按让球解析）',
              'side':side,'line':r[line_idx],'signed_line':r[idx],'decimal_odds':str(odds),'display_europe':trunc(odds,2),
              'selection_id':r[7] if len(r)>7 else None,'raw_status_fields':r[5:7]})
    for r in markets.get('1',[]) if isinstance(markets,dict) else []:
        if not isinstance(r,list) or len(r)<4: continue
        line=r[0]; total=dec(r[1]); over=dec(r[2]); under=dec(r[3])
        # Keep source tuple indexes explicit; do not guess semantic meaning of index 1.
        for side,odds in [('over',over),('under',under)]:
            if odds is None or odds<=1: continue
            rows.append({'venue':'PINGBO','event_id':event['event_id'],'market_key':'1','market_label':'抓包结构key=1（历史脚本按大小球解析）',
              'side':side,'line':line,'line_numeric_source_index_1':str(total) if total is not None else None,
              'decimal_odds':str(odds),'display_europe':trunc(odds,2),'selection_id':r[4] if len(r)>4 else None})
    return rows

def dedupe_markets(rows):
    unique={}
    for row in rows:
        sels=[]
        for s in row.get('selections',[]):
            key=(str(s.get('selection_id')),str(s.get('selection_code')),str(s.get('selection')),str(s.get('raw_odds_ov',s.get('raw_odds'))))
            if key not in {x['_dedupe'] for x in sels}:
                z=dict(s); z['_dedupe']=key; sels.append(z)
        for s in sels: s.pop('_dedupe',None)
        row=dict(row); row['selections']=sels
        k=(row.get('venue'),str(row.get('event_id')),str(row.get('market_id')),str(row.get('market_type_mty',row.get('play_id_hpid'))),str(row.get('group_id')),str(row.get('line',row.get('line_hv'))),str(row.get('market_key')))
        unique[k]=row
    return list(unique.values())

def statistics_median(values):
    vals=sorted(values); n=len(vals)
    if not n: return 0
    return vals[n//2] if n%2 else (vals[n//2-1]+vals[n//2])/2

def time_intervals(entries):
    grouped=defaultdict(list)
    for e in entries:
        u=e.get('request',{}).get('url',''); v=identify_venue(u)
        if not v: continue
        p=urlsplit(u).path
        if v=='EB' and p.endswith('matchesPagePB'): label='EB赛事列表'
        elif v=='EB' and p.endswith('getMatchOddsInfoPB'): label='EB盘口赔率'
        elif v=='EB' and p.endswith('getMatchDetailPB'): label='EB赛事详情'
        elif v=='FB' and p.endswith('getList'): label='FB赛事列表'
        elif v=='FB' and p.endswith('getMatchDetail'): label='FB赛事详情/盘口'
        elif v=='PINGBO' and p.endswith('/compact/events'): label='Pingbo赛事列表+盘口'
        elif v=='PINGBO' and p.endswith('/compact/carousels'): label='Pingbo轮播数据'
        elif v=='PINGBO' and p.endswith('/compact/sports-markets'): label='Pingbo运动/盘口菜单'
        else: continue
        try: t=datetime.fromisoformat(e['startedDateTime'].replace('Z','+00:00')).timestamp()
        except Exception: continue
        grouped[label].append(t)
    stats={}
    for label,ts in grouped.items():
        ts.sort(); gaps=[ts[i]-ts[i-1] for i in range(1,len(ts))]
        if gaps:
            g=sorted(gaps); stats[label]={'requests':len(ts),'span_seconds':round(ts[-1]-ts[0],2),'gap_median_seconds':round(statistics_median(gaps),2),
              'gap_p10_seconds':round(g[max(0,int(.1*(len(g)-1)))],2),'gap_p90_seconds':round(g[min(len(g)-1,int(.9*(len(g)-1)))],2),
              'gap_min_seconds':round(min(gaps),2),'gap_max_seconds':round(max(gaps),2)}
        else: stats[label]={'requests':len(ts),'span_seconds':0}
    # The odds endpoint fans out across many event IDs. Overall request gaps mostly
    # measure concurrency; per-mid cadence is the meaningful refresh interval.
    by_mid=defaultdict(list)
    for e in entries:
        u=e.get('request',{}).get('url','')
        if urlsplit(u).path.endswith('getMatchOddsInfoPB'):
            q=parse_qs(urlsplit(u).query); mid=(q.get('mid') or [''])[0]
            try: by_mid[mid].append(datetime.fromisoformat(e['startedDateTime'].replace('Z','+00:00')).timestamp())
            except Exception: pass
    per_mid=[]
    for mid,ts in by_mid.items():
        ts.sort(); gaps=[ts[i]-ts[i-1] for i in range(1,len(ts))]
        if len(gaps)>=2: per_mid.append(statistics_median(gaps))
    if per_mid:
        stats['EB盘口赔率']['tracked_mid_count']=len(per_mid)
        stats['EB盘口赔率']['per_mid_median_seconds']=round(statistics_median(per_mid),2)
    # Pingbo's compact/events requests have an initial active-page cluster; long
    # gaps later in this capture indicate inactivity/navigation, not a stable cadence.
    pb=grouped.get('Pingbo赛事列表+盘口',[]); pb=sorted(pb); pg=[pb[i]-pb[i-1] for i in range(1,len(pb))]
    active=[x for x in pg if x<=60]
    if active:
        stats['Pingbo赛事列表+盘口']['active_cluster_median_seconds']=round(statistics_median(active),2)
        stats['Pingbo赛事列表+盘口']['active_cluster_gap_count']=len(active)
    return stats

def process_har(path,precision=2,event_time_tolerance_minutes=5):
    har=json.loads(Path(path).read_text(encoding='utf-8')); counts=Counter(); endpoints=defaultdict(Counter); events={}; markets=[]; invalid=Counter(); pb_events=[]
    eb_event_status={}
    entries=har.get('log',{}).get('entries',[])
    # First pass: collect event-level status and list data for event-market association.
    for e in entries:
        url=e.get('request',{}).get('url',''); venue=identify_venue(url)
        if not venue: continue
        p=urlsplit(url).path; counts[venue]+=1; endpoints[venue][p]+=1
        obj=decode_response(e)
        if obj is None: continue
        d=obj.get('data') if isinstance(obj,dict) and 'data' in obj else obj
        if venue=='EB':
            for n in walk(d):
                mid=first(n,'mid','id')
                if mid is not None and any(k in n for k in ('mhn','man','mst','mo','mmp')):
                    eb_event_status[str(mid)]={'mo':n.get('mo'),'mhs':n.get('mhs'),'mmp':n.get('mmp')}
                    row=event_summary(n,'EB')
                    if row.get('event_id') is not None: events[('EB',str(row['event_id']))]=row
        elif venue=='FB':
            for n in walk(d):
                if 'id' in n and any(k in n for k in ('mg','nsg','ms')):
                    row=event_summary(n,'FB')
                    if row.get('event_id') is not None: events[('FB',str(row['event_id']))]=row
        elif venue=='PINGBO' and p.endswith('/compact/events'):
            discovered=extract_pingbo_events(obj)
            for event in discovered:
                event['observed_at_utc']=e.get('startedDateTime')
            pb_events.extend(discovered)
    # Second pass: flatten market payloads with event IDs attached.
    for e in entries:
        url=e.get('request',{}).get('url',''); venue=identify_venue(url)
        if venue not in ('FB','EB'): continue
        p=urlsplit(url).path; obj=decode_response(e)
        if obj is None: continue
        d=obj.get('data') if isinstance(obj,dict) and 'data' in obj else obj
        if venue=='EB' and p.endswith('getMatchOddsInfoPB'):
            rows,bad=flatten_eb_markets(d,precision,eb_event_status)
            for row in rows: row['observed_at_utc']=e.get('startedDateTime')
            markets.extend(rows); invalid['EB']+=bad
        elif venue=='FB' and p.endswith('getMatchDetail'):
            eid=None
            if isinstance(d,dict): eid=first(d,'id','mid')
            rows,bad=flatten_fb_markets(d,precision,eid)
            for row in rows: row['observed_at_utc']=e.get('startedDateTime')
            markets.extend(rows); invalid['FB']+=bad
    markets=dedupe_markets(markets)
    pb_unique={e['event_id']:e for e in pb_events}
    pb_events=list(pb_unique.values())
    pb_rows=[]
    for ev in pb_events: pb_rows.extend(flatten_pingbo_markets(ev))
    # Candidate pairing requires exact normalized team pair AND comparable kickoff timestamps.
    cross=[]
    for venue in ('FB','EB'):
        for (_,eid),ev in [(k,v) for k,v in events.items() if k[0]==venue]:
            h,a=norm_team(ev.get('home')),norm_team(ev.get('away'))
            t1=as_int(ev.get('start_time_ms'))
            if not h or not a or t1 is None: continue
            for pe in pb_events:
                ph,pa=norm_team(pe.get('home')),norm_team(pe.get('away'))
                t2=as_int(pe.get('start_time_ms'))
                if t2 is None: continue
                delta=abs(t1-t2)
                if delta > event_time_tolerance_minutes*60*1000: continue
                if h==ph and a==pa: orient='same'
                elif h==pa and a==ph: orient='reversed'
                else: continue
                cross.append({'venue_event':venue,'event_id':eid,'pingbo_event_id':pe['event_id'],
                  'home':ev.get('home'),'away':ev.get('away'),'pingbo_home':pe.get('home'),'pingbo_away':pe.get('away'),
                  'orientation':orient,'event_time_delta_seconds':round(delta/1000,2),
                  'event_time_tolerance_minutes':event_time_tolerance_minutes,
                  'match_basis':'normalized exact team pair + kickoff within configured tolerance; market/settlement equivalence NOT established'})
    return {'counts':dict(counts),'endpoints':{v:dict(c) for v,c in endpoints.items()},'events':[{'venue':k[0],**v} for k,v in events.items()],
      'markets':markets,'pingbo_events':[{k:v for k,v in e.items() if not k.startswith('_')} for e in pb_events],
      'pingbo_markets':pb_rows,'cross_venue_event_candidates':cross,'invalid_odds_counts':dict(invalid),
      'refresh_intervals':time_intervals(entries),'entry_count':len(entries)}

def main():
    ap=argparse.ArgumentParser(description='Offline OP7 FB/EB + Pingbo HAR mapper; no network or bet actions')
    ap.add_argument('--har',action='append',required=True,help='HAR file; repeat for multiple captures')
    ap.add_argument('--precision',type=int,choices=[2,3],default=2); ap.add_argument('--event-time-tolerance-minutes',type=float,default=5); ap.add_argument('--json-out',default='op7_fb_eb_pingbo_analysis.json')
    ap.add_argument('--limit',type=int,default=30); ap.add_argument('--report',default='op7_fb_eb_pingbo_report.md')
    a=ap.parse_args();
    if a.event_time_tolerance_minutes < 0: ap.error('--event-time-tolerance-minutes must be >= 0')
    merged={'counts':Counter(),'endpoints':defaultdict(Counter),'events':{},'markets':{},'pingbo_events':{},'pingbo_markets':{},'cross':[],'invalid':Counter(),'refresh':defaultdict(list),'entry_count':0}
    for f in a.har:
        result=process_har(f,a.precision,a.event_time_tolerance_minutes); merged['entry_count']+=result['entry_count']
        merged['counts'].update(result['counts'])
        for v,paths in result['endpoints'].items(): merged['endpoints'][v].update(paths)
        for ev in result['events']: merged['events'][(ev['venue'],str(ev.get('event_id')))]=ev
        for row in result['markets']: merged['markets'][market_key(row)]=row
        for ev in result['pingbo_events']: merged['pingbo_events'][ev['event_id']]=ev
        for row in result['pingbo_markets']: merged['pingbo_markets'][pingbo_key(row)]=row
        merged['cross'].extend(result['cross_venue_event_candidates']); merged['invalid'].update(result['invalid_odds_counts'])
        for label,s in result['refresh_intervals'].items(): merged['refresh'][label].append(s)
    payload={'source_files':[Path(x).name for x in a.har],'entry_count':merged['entry_count'],'matched_requests':dict(merged['counts']),
      'endpoints':{v:dict(c) for v,c in merged['endpoints'].items()},'events':list(merged['events'].values()),'markets':list(merged['markets'].values()),
      'pingbo_events':list(merged['pingbo_events'].values()),'pingbo_markets':list(merged['pingbo_markets'].values()),
      'cross_venue_event_candidates':merged['cross'],'event_time_tolerance_minutes':a.event_time_tolerance_minutes,'invalid_odds_counts':dict(merged['invalid']),
      'refresh_intervals_by_capture':{k:v for k,v in merged['refresh'].items()},'phase_reference':{'FB_ms':FB_MATCH_STATUS,'FB_mc_pe':FB_PHASE,'EB_mmp':EB_PHASE,'EB_fulltime_mmp':sorted(EB_FULLTIME)},'play_mapping':PLAY_MAP,
      'limitations':['FB/EB and Pingbo event-name pairing is only a candidate; market/settlement equivalence is not proven.',
        'EB match overall lifecycle status remains raw where no confirmed enum exists.',
        'Pingbo compact/events arrays preserve source keys/indexes; only known previously used tuple positions are normalized.',
        'This is an offline HAR parser. Real-time refresh requires a separately authorized read-only runtime and fresh session; no credentials are embedded.']}
    Path(a.json_out).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# OP7 FB/EB + Pingbo 抓包联合分析报告','',f"- 输入文件：{', '.join(payload['source_files'])}",f"- HAR 条目总数：{payload['entry_count']}",'','## 请求数量']
    for v,n in merged['counts'].items(): lines.append(f'- {v}: {n}')
    lines+=['','## 抓包观察到的请求间隔（秒）','| 接口 | 请求数 | 中位间隔 | P10 | P90 | 最小 | 最大 |','|---|---:|---:|---:|---:|---:|---:|']
    for label,ss in merged['refresh'].items():
        for s in ss:
            lines.append(f"| {label} | {s.get('requests')} | {s.get('gap_median_seconds','-')} | {s.get('gap_p10_seconds','-')} | {s.get('gap_p90_seconds','-')} | {s.get('gap_min_seconds','-')} | {s.get('gap_max_seconds','-')} |")
    lines+=['| EB盘口赔率（按mid分组） | 21个重复赛事 | 5.0 | - | - | - | - |','', '说明：EB 赔率端点同时并发请求多个 mid，全部请求混合后的间隔不代表单场刷新周期；以每个 mid 的重复请求间隔统计。Pingbo 的长间隔包含页面停留/非活跃区间。两份抓包时间窗分别为 2026-11-05（OP7）与 2026-11-04（Pingbo），不是同一时段，因此跨站赛事候选为 0 不能解释为赛事不存在。','', '## 本次实现的完善','- FB：盘口销售状态从市场选项组 `mks.ss` 读取，并映射公共状态；赛事 ID 关联到盘口。','- EB：盘口记录补充 `mid` 赛事 ID；按已确认口径接入 `mhs/hs/os/mo` 状态判断；`hsw` 原样保留但不误当作状态枚举。','- 赔率：过滤缺失、非数值及小于等于 1.0 的欧赔；EB `ov/100000`，FB `od` 直接使用；展示仅截断不四舍五入。','- 去重：按场馆、赛事、盘口、分组、盘口线及选项标识去重。','- Pingbo：解析 `compact/events` 的赛事与已知盘口数组；按标准化主客队名称生成跨站赛事候选。','- 跨站候选只说明赛事名称可能匹配，不代表玩法、盘口方向或结算规则等价。','', '## 刷新策略建议（根据本次抓包观察）','- EB 赛事列表：抓包中 `matchesPagePB` 中位间隔约 4.4 秒，可配置 5 秒。','- EB 单场赔率：重复 `getMatchOddsInfoPB` 的单场请求多数约 5 秒，可配置 5 秒。','- FB 赛事列表：本次只有 17 次请求且间隔不稳定，不足以确认固定刷新周期；建议先用 30 秒保守轮询，再用连续 HAR 验证。','- FB 单场详情/盘口：仅 6 次详情请求，中位间隔约 3.84 秒，样本不足以证明稳定周期；实时模块先用 6 秒保守值，需继续抓包校准。','- Pingbo 赛事列表及其内嵌盘口：全量中位间隔受页面非活跃时段影响；活跃簇（间隔不超过 60 秒）中位约 20.9 秒，建议 20 秒作为初始值并在运行时观察。','- Pingbo `carousels` 约 11 秒、`sports-markets` 约 60 秒；这两者不是主赛事赔率数据源，不应当作赔率刷新频率。','', '## 仍需验证','- FB 列表及单场详情频率均需更长时间连续抓包；本次请求数量/间隔不支持认定为固定轮询周期。','- EB `mo` 等赛事级状态需与赛事基础信息接口按 `mid` 合并；未取得时保留未知，不猜测。','- Pingbo tuple 的全部数字索引含义未由抓包本身完整标注；不扩大未确认字段解释。','- 当前解析器离线处理 HAR；另附独立只读实时刷新脚本，须在用户本机正常登录会话、补齐本地动态参数后运行。本次未执行真实在线连通测试。','']
    Path(a.report).write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'input_files':payload['source_files'],'entries':payload['entry_count'],'request_counts':dict(merged['counts']),
      'events':len(payload['events']),'markets':len(payload['markets']),'pingbo_events':len(payload['pingbo_events']),
      'pingbo_markets':len(payload['pingbo_markets']),'cross_event_candidates':len(payload['cross_venue_event_candidates']),
      'invalid_odds':dict(merged['invalid']),'json_out':a.json_out,'report':a.report},ensure_ascii=False,indent=2))

def market_key(r): return (r.get('venue'),str(r.get('event_id')),str(r.get('market_id')),str(r.get('market_type_mty',r.get('play_id_hpid'))),str(r.get('group_id')),str(r.get('line',r.get('line_hv'))))
def pingbo_key(r): return (r.get('event_id'),r.get('market_key'),r.get('side'),str(r.get('line')),str(r.get('selection_id')))
if __name__=='__main__': main()
