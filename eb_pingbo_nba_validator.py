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
def get_eb_under(snapshot_dir):
    es=load_snapshot(snapshot_dir/"EB_events_latest.json")
    ms=load_snapshot(snapshot_dir/"EB_markets_latest.json")
    evmap={str(e.get("id")):e for e in es.get("data",[]) if isinstance(e,dict)}
    rows=[]
    for m in ms.get("data",[]):
        if not isinstance(m,dict) or str(m.get("play_id_hpid"))!="38": continue
        ev=evmap.get(str(m.get("event_id")),{})
        if not is_nba(ev): continue
        for s in m.get("selections",[]):
            if not is_under(s): continue
            odds=dec(s.get("decimal_odds")); line=dec(m.get("line_hv"))
            if odds is None or odds<=1 or line is None: continue
            rows.append({"event_id":str(m.get("event_id")),"home_team":ev.get("home"),"away_team":ev.get("away"),
             "league":ev.get("league") or ev.get("competition"),"period":"FULL_GAME","selection":"Under",
             "points":str(line.normalize()),"odds_decimal":str(odds),"market_id":m.get("market_id"),
             "group_id":m.get("group_id"),"selection_id":s.get("selection_id"),
             "market_status":s.get("common_market_status",m.get("group_status_hs")),
             "observed_at_utc":m.get("observed_at_utc")})
    return rows,{"eb_events":len(evmap),"eb_market_groups":len(ms.get("data",[])),
                 "eb_event_status":es.get("status"),"eb_market_status":ms.get("status")}
def run_validation(eb_rows,pb,min_edge):
    pbmap={}
    for ev in pb.get("events",[]):
        k=(team_key(ev.get("home_team_en") or ev.get("home_team")),team_key(ev.get("away_team_en") or ev.get("away_team")))
        if all(k): pbmap[k]=ev
    out=[]
    for eb in eb_rows:
        k=(team_key(eb.get("home_team")),team_key(eb.get("away_team")))
        pe=pbmap.get(k)
        base={**eb,"pingbo_selection":"Over"}
        if not pe:
            out.append({**base,"rule_status":"NO_EVENT_MATCH","rule_reason":"平博 NBA 列表未找到主客队顺序一致的同场赛事"}); continue
        lines=[x for x in pe.get("full_game_totals",[]) if str(x.get("points"))==eb["points"] and not x.get("unavailable") and not x.get("offline")]
        if not lines:
            out.append({**base,"pingbo_event_id":pe.get("event_id"),"rule_status":"NO_REVERSE_LINE","rule_reason":"平博详情没有相同总分线的可用 Over 盘口"}); continue
        for line in lines:
            po=dec(line.get("over_odds_decimal")); eo=dec(eb["odds_decimal"])
            if po is None or po<=1:
                out.append({**base,"pingbo_event_id":pe.get("event_id"),"rule_status":"ODDS_UNAVAILABLE","rule_reason":"平博 Over 赔率无效或未开放"}); continue
            total=Decimal(1)/eo+Decimal(1)/po
            edge=Decimal(1)-total
            roi=edge/total*100 if total else Decimal(0)
            passed=edge>min_edge
            out.append({**base,"pingbo_event_id":pe.get("event_id"),"pingbo_home_team":pe.get("home_team"),
             "pingbo_away_team":pe.get("away_team"),"pingbo_points":line.get("points"),"pingbo_line_id":line.get("line_id"),
             "pingbo_odds_american":line.get("over_odds_american"),"pingbo_odds_decimal":str(po.quantize(Decimal(".001"))),
             "calculation":{"eb_implied_probability":str((100/eo).quantize(Decimal(".001")))+"%",
              "pingbo_implied_probability":str((100/po).quantize(Decimal(".001")))+"%",
              "combined_implied_probability":str((total*100).quantize(Decimal(".001")))+"%",
              "edge_percent":str((edge*100).quantize(Decimal(".001")))+"%",
              "theoretical_roi_percent":str(roi.quantize(Decimal(".001")))+"%",
              "formula":"1 - (1/EB_Under_decimal + 1/Pingbo_Over_decimal)"},
             "rule_status":"RULE_PASS" if passed else "RULE_FAIL",
             "rule_reason":"合并隐含概率低于100%且达到阈值" if passed else "合并隐含概率未低于100%或未达到阈值"})
    return {"generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "rule":{"name":"two_way_arbitrage_screen","min_edge_percent":str(min_edge*100),
       "pass_condition":"1/EB_Under + 1/Pingbo_Over < 1 - min_edge"},
      "summary":{"eb_under_rows":len(eb_rows),"rule_pass":sum(x["rule_status"]=="RULE_PASS" for x in out),
       "rule_fail":sum(x["rule_status"]=="RULE_FAIL" for x in out),
       "unmatched":sum(x["rule_status"] not in ("RULE_PASS","RULE_FAIL") for x in out)},
      "results":out,"safety":"只读校验，不含投注、订单、钱包或资金接口"}
def markdown(r):
    lines=["# EB Under × Pingbo NBA 反向大小分校验","",
     f"时间：{r['generated_at_utc']}","",
     f"通过 {r['summary']['rule_pass']} | 未通过 {r['summary']['rule_fail']} | 未匹配 {r['summary']['unmatched']}","",
     "| EB赛事 | EB盘口 | EB赔率 | 平博反向盘口 | 平博赔率 | 合并概率 | Edge | 判断 |",
     "|---|---:|---:|---:|---:|---:|---:|---|"]
    for x in r["results"]:
        c=x.get("calculation") or {}
        lines.append(f"| {x.get('home_team')} vs {x.get('away_team')} | 小 {x.get('points')} | {x.get('odds_decimal')} | {'大 '+str(x.get('pingbo_points') or x.get('points')) if x.get('pingbo_odds_decimal') else '—'} | {x.get('pingbo_odds_decimal','—')} | {c.get('combined_implied_probability','—')} | {c.get('edge_percent','—')} | {x.get('rule_status')} |")
    lines += ["","计算：Edge = 1 - (1 / EB Under十进制赔率 + 1 / 平博 Over十进制赔率)。Edge 大于配置阈值才通过。",
      "这是两边赔率的理论套利筛查，不代表预测；程序不会自动投注。"]
    return "\n".join(lines)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--snapshots",default="live_snapshots"); p.add_argument("--out",default="eb_pingbo_nba_validation.json")
    p.add_argument("--report",default="eb_pingbo_nba_validation.md"); p.add_argument("--min-edge-percent",type=Decimal,default=Decimal("0"))
    p.add_argument("--interval",type=int,default=20); p.add_argument("--watch",action="store_true"); p.add_argument("--timeout",type=int,default=15)
    a=p.parse_args()
    if a.interval<5: p.error("--interval must be >=5")
    client=PingboClient(timeout=a.timeout)
    def once():
        eb,meta=get_eb_under(Path(a.snapshots)); pb=client.fetch_nba_details()
        report=run_validation(eb,pb,a.min_edge_percent/100)
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
