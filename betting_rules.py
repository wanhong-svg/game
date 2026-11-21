"""Configuration-driven preflight checks; never submits bets."""
from __future__ import annotations
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

class BettingRulesConfigError(ValueError):
    pass

@dataclass(frozen=True)
class RuleDecision:
    allowed: bool
    code: str
    message: str

def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)

def validate_config(config: Any) -> dict:
    if not isinstance(config, dict):
        raise BettingRulesConfigError("配置根节点必须是 JSON 对象")
    if not isinstance(config.get("enabled"), bool):
        raise BettingRulesConfigError("enabled 必须为 true 或 false")
    plays = config.get("allowed_plays")
    if not isinstance(plays, dict) or not plays:
        raise BettingRulesConfigError("allowed_plays 必须是非空对象")
    if any(not isinstance(k, str) or not k.strip() for k in plays):
        raise BettingRulesConfigError("玩法名称必须是非空字符串")
    if any(not isinstance(v, bool) for v in plays.values()):
        raise BettingRulesConfigError("每个玩法的开关值必须为 true 或 false")
    limits = config.get("limits")
    if not isinstance(limits, dict):
        raise BettingRulesConfigError("limits 必须是对象")
    cap = limits.get("max_tickets_per_match_play")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise BettingRulesConfigError("max_tickets_per_match_play 必须是大于等于 1 的整数")
    stake = config.get("stake")
    if not isinstance(stake, dict):
        raise BettingRulesConfigError("stake 必须是对象")
    low, high = stake.get("min"), stake.get("max")
    if not _is_number(low) or not _is_number(high):
        raise BettingRulesConfigError("stake.min 和 stake.max 必须是数字")
    if not math.isfinite(low) or not math.isfinite(high) or low <= 0 or high < low:
        raise BettingRulesConfigError("金额范围无效：要求 0 < min <= max，且必须是有限数字")
    return config

def load_rules(path: str | Path = "config/betting_rules.json") -> dict:
    """Load and validate rules; call at every application startup."""
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BettingRulesConfigError(f"找不到规则配置文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise BettingRulesConfigError(f"规则配置不是有效 JSON：{exc}") from exc
    return validate_config(config)

def evaluate_bet(rules: dict, *, match_id: str, play: str, stake: float,
                 existing_ticket_count: int) -> RuleDecision:
    """Check an action using caller-supplied count for this exact match + play."""
    try:
        validate_config(rules)
    except BettingRulesConfigError as exc:
        return RuleDecision(False, "INVALID_CONFIG", str(exc))
    if not rules["enabled"]:
        return RuleDecision(False, "RULES_DISABLED", "规则总开关已关闭，默认拦截")
    if not isinstance(match_id, str) or not match_id.strip():
        return RuleDecision(False, "INVALID_MATCH", "比赛 ID 不能为空")
    if not isinstance(play, str) or not play.strip():
        return RuleDecision(False, "INVALID_PLAY", "玩法名称不能为空")
    if play not in rules["allowed_plays"]:
        return RuleDecision(False, "PLAY_NOT_CONFIGURED", f"玩法“{play}”未配置，默认拦截")
    if not rules["allowed_plays"][play]:
        return RuleDecision(False, "PLAY_DISABLED", f"玩法“{play}”未启用")
    if not _is_number(stake) or not math.isfinite(stake):
        return RuleDecision(False, "INVALID_STAKE", "投注金额必须是有限数字")
    if stake < rules["stake"]["min"] or stake > rules["stake"]["max"]:
        return RuleDecision(False, "STAKE_OUT_OF_RANGE",
                            f"金额必须在 {rules['stake']['min']} 至 {rules['stake']['max']} 之间")
    if isinstance(existing_ticket_count, bool) or not isinstance(existing_ticket_count, int) or existing_ticket_count < 0:
        return RuleDecision(False, "INVALID_TICKET_COUNT", "已有注单数量必须是非负整数")
    cap = rules["limits"]["max_tickets_per_match_play"]
    if existing_ticket_count >= cap:
        return RuleDecision(False, "TICKET_LIMIT_REACHED",
                            f"该场比赛的“{play}”玩法已有 {existing_ticket_count} 单，数量上限为 {cap}")
    return RuleDecision(True, "ALLOWED", "通过本地规则校验")
