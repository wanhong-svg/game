#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only EB Under vs Pingbo reverse-side NBA totals validator."""
from __future__ import annotations
import argparse, json, re, time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from pingbo_nba_detail import PingboClient, normalize_team_name

ALIASES = {
"奥兰多魔术队":"orlandomagic","奥兰多魔术":"orlandomagic","魔术":"orlandomagic",
"底特律活塞队":"detroitpistons","底特律活塞":"detroitpistons","活塞":"detroitpistons",
"华盛顿奇才队":"washingtonwizards","华盛顿奇才":"washingtonwizards","奇才":"washingtonwizards",
"波士顿凯尔特人队":"bostonceltics","波士顿凯尔特人":"bostonceltics","凯尔特人":"bostonceltics",
"丹佛掘金队":"denvernuggets","丹佛掘金":"denvernuggets","掘金":"denvernuggets",
"达拉斯独行侠队":"dallasmavericks","达拉斯独行侠":"dallasmavericks","达拉斯小牛队":"dallasmavericks","达拉斯小牛":"dallasmavericks",
"洛杉矶湖人队":"losangeleslakers","洛杉矶湖人":"losangeleslakers","湖人":"losangeleslakers",
"金州勇士队":"goldenstatewarriors","金州勇士":"goldenstatewarriors","勇士":"goldenstatewarriors",
"菲尼克斯太阳队":"phoenixsuns","菲尼克斯太阳":"phoenixsuns","太阳":"phoenixsuns",
"密尔沃基雄鹿队":"milwaukeebucks","密尔沃基雄鹿":"milwaukeebucks","雄鹿":"milwaukeebucks",
"纽约尼克斯队":"newyorkknicks","纽约尼克斯":"newyorkknicks","尼克斯":"newyorkknicks",
"布鲁克林篮网队":"brooklynnets","布鲁克林篮网":"brooklynnets","篮网":"brooklynnets",
"费城76人队":"philadelphia76ers","费城76人":"philadelphia76ers","76人":"philadelphia76ers",
"迈阿密热火队":"miamiheat","迈阿密热火":"miamiheat","热火":"miamiheat",
"芝加哥公牛队":"chicagobulls","芝加哥公牛":"chicagobulls","公牛":"chicagobulls",
"克利夫兰骑士队":"clevelandcavaliers","克利夫兰骑士":"clevelandcavaliers","骑士":"clevelandcavaliers",
"亚特兰大老鹰队":"atlantahawks","亚特兰大老鹰":"atlantahawks","老鹰":"atlantahawks",
"多伦多猛龙队":"torontoraptors","多伦多猛龙":"torontoraptors","猛龙":"torontoraptors",
"印第安纳步行者队":"indianapacers","印第安纳步行者":"indianapacers","步行者":"indianapacers",
"明尼苏达森林狼队":"minnesotatimberwolves","明尼苏达森林狼":"minnesotatimberwolves","森林狼":"minnesotatimberwolves",
"俄克拉荷马雷霆队":"oklahomacitythunder","俄克拉荷马雷霆":"oklahomacitythunder","雷霆":"oklahomacitythunder",
"萨克拉门托国王队":"sacramentokings","萨克拉门托国王":"sacramentokings","国王":"sacramentokings",
"新奥尔良鹈鹕队":"neworleanspelicans","新奥尔良鹈鹕":"neworleanspelicans","鹈鹕":"neworleanspelicans",
"孟菲斯灰熊队":"memphisgrizzlies","孟菲斯灰熊":"memphisgrizzlies","灰熊":"memphisgrizzlies",
"休斯顿火箭队":"houstonrockets","休斯顿火箭":"houstonrockets","火箭":"houstonrockets",
"圣安东尼奥马刺队":"sanantoniospurs","圣安东尼奥马刺":"sanantoniospurs","马刺":"sanantoniospurs",
"波特兰开拓者队":"portlandtrailblazers","波特兰开拓者":"portlandtrailblazers","开拓者":"portlandtrailblazers",
"犹他爵士队":"utahjazz","犹他爵士":"utahjazz","爵士":"utahjazz",
"夏洛特黄蜂队":"charlottehornets","夏洛特黄蜂":"charlottehornets","黄蜂":"charlottehornets",
}
def team_key(v):
    if not v: return ""
    s=str(v).strip()
    return ALIASES.get(s, normalize_team_name(s))
def dec(v):
    try: return Decimal(str(v))
    except (InvalidOperation,ValueError,TypeError): return None
def is_nba(ev):
    s=" ".join(str(ev.get(k) or "") for k in ("sport","sport_name","league","competition","league_name")).lower()
    return "nba" in s or "美国职业篮球" in s or "national basketball" in s
def is_under(s):
    t=" ".join(str(s.get(k) or "") for k in ("selection","selection_code","side_code","source_code")).lower()
    return bool(re.search(r"(^|[^a-z])(under|u)([^a-z]|$)|小分|小于|小",t))
def load_snapshot(p):
    if not p.exists(): raise FileNotFoundError(f"缺少快照：{p}")
    x=json.loads(p.read_text(encoding="utf-8"))
    if x.get("status") not in (None,"SUCCESS","PARTIAL"): raise RuntimeError(f"{p.name} 状态异常：{x.get('status')}")
    return x
def is_over(s):
    t=" ".join(str(s.get(k) or "") for k in ("selection","selection_code","side_code","source_code")).lower()
    return bool(re.search(r"(^|[^a-z])(over|o)([^a-z]|$)|大分|大于|大",t))

def normalize_start_time(value):
    if value in (None, ""):
        return None
    try:
        n=int(value)
        # EB/Pingbo timestamps are expected as Unix seconds or milliseconds.
        return n if abs(n)<100000000000 else n//1000
    except (ValueError, TypeError):
        return None

def team_pair(ev):
    a=team_key(ev.get("home_team_en") or ev.get("home_team") or ev.get("home"))
    b=team_key(ev.get("away_team_en") or ev.get("away_team") or ev.get("away"))
    return tuple(sorted((a,b))) if a and b else None

def event_is_live(ev, venue):
    explicit=ev.get("is_live")
    if isinstance(explicit,bool):
        return explicit
    status=str(ev.get("raw_status") or ev.get("status") or "").strip().lower()
    if status in {"live","inplay","in_play","进行中","滚球"}:
        return True
    if status in {"scheduled","not_started","未开赛","即将开始","prematch"}:
        return False
    phase=ev.get("phase_pe") or ev.get("phase_mmp") if venue=="EB" else ev.get("phase_code")
    if phase is None:
        label=str(ev.get("period_label") or "").strip().lower()
        if label in {"q1","q2","q3","q4","1st quarter","2nd quarter","3rd quarter","4th quarter","overtime","加时","第一节","第二节","第三节","第四节","中场","上半场","下半场"}:
            return True
        if label in {"not started","scheduled","未开赛","即将开始"}:
            return False
        return None
    # EB phase values below are taken from the existing event phase mapping.
    if venue=="EB":
        try: p=int(phase)
        except (ValueError,TypeError): return None
        if p==0: return False
        if p in {6,31,302,7,41,42}: return True
    return None

def live_progress_signature(ev):
    # Only compare explicitly normalized fields; do not equate vendor-specific
    # numeric enums or guess the meaning of an undocumented clock field.
    period=ev.get("period_label") or ev.get("phase_code")
    clock=ev.get("clock_seconds")
    if period in (None,"") or clock in (None,""):
        return None
    try: clock=int(clock)
    except (ValueError,TypeError): return None
    return (str(period).strip().lower(), clock)

def get_eb_under(snapshot_dir):
    es=load_snapshot(snapshot_dir/"EB_events_latest.json")
    ms=load_snapshot(snapshot_dir/"EB_markets_latest.json")
    evmap={str(e.get("id")):e for e in es.get("data",[]) if isinstance(e,dict)}
    rows=[]
    for m in ms.get("data",[]):
        if not isinstance(m,dict) or str(m.get("play_id_hpid"))!="38": continue
        # hpid=38 is shared by ordinary full-game totals and additional totals.
        # Only the ordinary market belongs in this EB Under × Pingbo validator.
        market_name=str(m.get("market_name") or "")
        top_key=str(m.get("market_top_key") or m.get("play_id_hpid") or "")
        if market_name!="全场大小" or top_key!="38": continue
        ev=evmap.get(str(m.get("event_id")),{})
        if not is_nba(ev): continue
        selections=m.get("selections",[])
        under=next((s for s in selections if is_under(s)),None)
        over=next((s for s in selections if is_over(s)),None)
        if not under: continue
        under_odds=dec(under.get("decimal_odds"))
        over_odds=dec(over.get("decimal_odds")) if over else None
        line=dec(m.get("line_hv"))
        if under_odds is None or under_odds<=1 or line is None: continue
        rows.append({
            "event_id":str(m.get("event_id")),"home_team":ev.get("home"),"away_team":ev.get("away"),
            "league":ev.get("league") or ev.get("competition"),"period":"FULL_GAME","selection":"Under",
            "points":str(line.normalize()),"odds_decimal":str(under_odds),
            "over_odds_decimal":str(over_odds) if over_odds is not None and over_odds>1 else None,
            "market_id":m.get("market_id"),"group_id":m.get("group_id"),
            "market_name":market_name,"market_top_key":top_key,"play_id_hpid":m.get("play_id_hpid"),
            "selection_id":under.get("selection_id"),
            "market_status":under.get("common_market_status",m.get("group_status_hs")),
            "start_time_ms":ev.get("start_time_ms"),"raw_status":ev.get("raw_status"),
            "phase_pe":ev.get("phase_pe"),"period_label":ev.get("period_label"),
            "clock_seconds":ev.get("clock_seconds"),"is_live":ev.get("is_live"),
            "observed_at_utc":m.get("observed_at_utc")
        })
    return rows,{"eb_events":len(evmap),"eb_market_groups":len(ms.get("data",[])),
                 "eb_event_status":es.get("status"),"eb_market_status":ms.get("status")}

def run_validation(eb_rows,pb,min_edge=None):
    threshold=Decimal("1.0052")
    pbmap={}
    for ev in pb.get("events",[]):
        key=team_pair(ev)
        if key:
            pbmap.setdefault(key,[]).append(ev)
    out=[]
    for eb in eb_rows:
        base={**eb,"match_entry":"EB_FULL_GAME_UNDER_ONLY"}
        candidates=pbmap.get(team_pair(eb),[]) if team_pair(eb) else []
        if not candidates:
            out.append({**base,"rule_status":"NO_EVENT_MATCH","rule_reason":"平博未找到双方球队相同的 NBA 赛事"}); continue
        eb_time=normalize_start_time(eb.get("start_time_ms"))
        eligible=[]; rejected=[]
        for pe in candidates:
            pb_time=normalize_start_time(pe.get("start_time_ms"))
            if eb_time is None or pb_time is None or eb_time!=pb_time:
                rejected.append("开赛时间缺失或不一致")
                continue
            eb_live=event_is_live(eb,"EB")
            pb_live=event_is_live(pe,"PINGBO")
            if eb_live is None or pb_live is None or eb_live!=pb_live:
                rejected.append("比赛状态无法确认一致")
                continue
            if eb_live:
                eb_sig=live_progress_signature(eb)
                pb_sig=live_progress_signature(pe)
                if eb_sig is None or pb_sig is None or eb_sig!=pb_sig:
                    rejected.append("滚球节次/比赛进度缺失或不一致")
                    continue
            eligible.append(pe)
        if len(eligible)!=1:
            status="EVENT_AMBIGUOUS" if len(eligible)>1 else "EVENT_STATE_MISMATCH"
            reason="同一球队组合对应多场候选赛事" if len(eligible)>1 else (rejected[0] if rejected else "未能确认同一场比赛")
            out.append({**base,"rule_status":status,"rule_reason":reason}); continue
        pe=eligible[0]
        lines=[]
        for line in pe.get("full_game_totals",[]):
            if line.get("period")!="FULL_GAME" or line.get("unavailable") or line.get("offline"):
                continue
            eb_line=dec(eb.get("points")); pb_line=dec(line.get("points"))
            if eb_line is not None and pb_line is not None and eb_line>=pb_line:
                lines.append(line)
        if not lines:
            out.append({**base,"pingbo_event_id":pe.get("event_id"),"rule_status":"NO_REVERSE_LINE",
                        "rule_reason":"没有符合 EB 小分盘口分数 ≥ Pingbo 大分盘口分数的全场盘口"}); continue
        for line in lines:
            eb_under=dec(eb.get("odds_decimal"))
            eb_over=dec(eb.get("over_odds_decimal"))
            pb_over=dec(line.get("over_odds_decimal"))
            pb_under=dec(line.get("under_odds_decimal"))
            valid=lambda x: x is not None and x>1
            a_total=(Decimal(1)/pb_over+Decimal(1)/eb_under) if valid(pb_over) and valid(eb_under) else None
            b_total=(Decimal(1)/pb_under+Decimal(1)/eb_over) if valid(pb_under) and valid(eb_over) else None
            a_pass=a_total is not None and a_total<threshold
            b_pass=b_total is not None and b_total<threshold
            common={**base,"pingbo_event_id":pe.get("event_id"),
                "pingbo_home_team":pe.get("home_team"),"pingbo_away_team":pe.get("away_team"),
                "pingbo_points":line.get("points"),"pingbo_line_id":line.get("line_id"),
                "line_condition":f"{eb.get('points')} >= {line.get('points')}",
                "cross_checks":{
                    "A_pingbo_over_plus_eb_under":{"pingbo_odds_decimal":str(pb_over) if pb_over is not None else None,
                        "eb_odds_decimal":str(eb_under) if eb_under is not None else None,
                        "reciprocal_sum":str(a_total) if a_total is not None else None,"threshold":"1.0052","passed":a_pass},
                    "B_pingbo_under_plus_eb_over":{"pingbo_odds_decimal":str(pb_under) if pb_under is not None else None,
                        "eb_odds_decimal":str(eb_over) if eb_over is not None else None,
                        "reciprocal_sum":str(b_total) if b_total is not None else None,"threshold":"1.0052","passed":b_pass}
                }}
            if a_pass and b_pass:
                out.append({**common,"rule_status":"ANOMALY_BOTH_DIRECTIONS",
                    "rule_reason":"两个交叉方向同时低于 1.0052，标记异常，不生成候选赔率"})
            elif not a_pass and not b_pass:
                out.append({**common,"rule_status":"NO_MATCH",
                    "rule_reason":"两个交叉方向均未满足严格小于 1.0052"})
            elif a_pass:
                out.append({**common,"candidate_side":"EB_UNDER","candidate_odds_decimal":str(eb_under),
                    "pingbo_selection":"Over","pingbo_odds_decimal":str(pb_over),
                    "reciprocal_sum":str(a_total),"rule_status":"CANDIDATE_A",
                    "rule_reason":"仅 Pingbo 大分 + EB 小分方向满足阈值"})
            else:
                out.append({**common,"candidate_side":"EB_OVER","candidate_odds_decimal":str(eb_over),
                    "pingbo_selection":"Under","pingbo_odds_decimal":str(pb_under),
                    "reciprocal_sum":str(b_total),"rule_status":"CANDIDATE_B",
                    "rule_reason":"仅 Pingbo 小分 + EB 大分方向满足阈值"})
    return {"generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "rule":{"name":"EB_UNDER_ENTRY_TWO_WAY_CROSS_CHECK","entry_condition":"同一场正规 NBA；EB 全场小分盘口 >= Pingbo 全场大分盘口",
       "cross_checks":["1/Pingbo_Over + 1/EB_Under < 1.0052","1/Pingbo_Under + 1/EB_Over < 1.0052"],
       "both_pass":"ANOMALY_BOTH_DIRECTIONS","neither_pass":"NO_MATCH"},
      "summary":{"eb_under_rows":len(eb_rows),"candidate_a":sum(x["rule_status"]=="CANDIDATE_A" for x in out),
       "candidate_b":sum(x["rule_status"]=="CANDIDATE_B" for x in out),
       "anomalies":sum(x["rule_status"]=="ANOMALY_BOTH_DIRECTIONS" for x in out),
       "no_match":sum(x["rule_status"]=="NO_MATCH" for x in out),
       "unmatched":sum(x["rule_status"] not in ("CANDIDATE_A","CANDIDATE_B","ANOMALY_BOTH_DIRECTIONS","NO_MATCH") for x in out)},
      "results":out,"safety":"只读校验，不含投注、订单、钱包或资金接口"}

def markdown(r):
    lines=["# EB Under 入口 × Pingbo NBA 双向交叉赔率校验","",
      f"时间：{r['generated_at_utc']}","",
      f"候选A {r['summary']['candidate_a']} | 候选B {r['summary']['candidate_b']} | 双向异常 {r['summary']['anomalies']} | 无匹配 {r['summary']['no_match']} | 未确认赛事/进程 {r['summary']['unmatched']}","",
      "| EB赛事 | EB小分线/赔率 | Pingbo大分线/赔率 | A倒数和 | B倒数和 | 结果 |",
      "|---|---:|---:|---:|---:|---|"]
    for x in r["results"]:
        checks=x.get("cross_checks") or {}
        ca=checks.get("A_pingbo_over_plus_eb_under") or {}
        cb=checks.get("B_pingbo_under_plus_eb_over") or {}
        lines.append(f"| {x.get('home_team')} vs {x.get('away_team')} | 小 {x.get('points')} / {x.get('odds_decimal')} | 大 {x.get('pingbo_points','—')} / {ca.get('pingbo_odds_decimal','—')} | {ca.get('reciprocal_sum','—')} | {cb.get('reciprocal_sum','—')} | {x.get('rule_status')} |")
    lines += ["","入口仅取 EB 全场小分；赛事双方球队按无序组合识别，开赛时间必须一致。滚球还要求双方提供相同的规范化节次与秒级进度字段；字段缺失或不一致时拒绝匹配。",
      "盘口入口条件：EB Under line >= Pingbo Over line。交叉赔率：A=1/Pingbo Over + 1/EB Under；B=1/Pingbo Under + 1/EB Over；各自严格小于 1.0052。",
      "仅一个方向成立才生成对应 EB 赔率候选；两个方向同时成立标记异常；均不成立不产生候选。程序只读，不执行投注。"]
    return "\\n".join(lines)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--snapshots",default="live_snapshots"); p.add_argument("--out",default="eb_pingbo_nba_validation.json")
    p.add_argument("--report",default="eb_pingbo_nba_validation.md"); p.add_argument("--min-edge-percent",type=Decimal,default=Decimal("0"),help="兼容旧参数；当前赔率阈值固定为 1.0052")
    p.add_argument("--interval",type=int,default=20); p.add_argument("--watch",action="store_true"); p.add_argument("--timeout",type=int,default=15)
    a=p.parse_args()
    if a.interval<5: p.error("--interval must be >=5")
    client=PingboClient(timeout=a.timeout)
    def once():
        eb,meta=get_eb_under(Path(a.snapshots)); pb=client.fetch_nba_details()
        report=run_validation(eb,pb)
        report["source_meta"]={"eb":meta,"pingbo_nba_events":pb.get("nba_events"),"pingbo_errors":pb.get("errors",[])}
        Path(a.out).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        Path(a.report).write_text(markdown(report),encoding="utf-8")
        print(markdown(report))
    if not a.watch: once(); return
    print("实时只读校验已启动，Ctrl+C停止。")
    while True:
        try: once()
        except Exception as e: print(f"[校验失败] {type(e).__name__}: {e}")
        time.sleep(a.interval)
if __name__=="__main__": main()
