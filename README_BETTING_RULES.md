# 配置驱动的玩法规则校验

本模块仅提供本地规则校验，不会访问投注、下单、订单、钱包或任何资金接口，也不会创建或提交注单。仓库保持只读数据采集边界。

## 配置与重启生效

首次使用时复制示例文件：

    cp config/betting_rules.example.json config/betting_rules.json

正式配置文件 config/betting_rules.json 已加入 .gitignore，避免本机配置被误提交。修改后重启程序，并在程序启动时调用 load_rules() 重新读取配置。

示例配置：

    {
      "enabled": true,
      "allowed_plays": {
        "独赢": true,
        "让球": false,
        "大小球": false,
        "单双": false
      },
      "limits": {
        "max_tickets_per_match_play": 1
      },
      "stake": {
        "min": 50,
        "max": 500
      }
    }

- allowed_plays：每个玩法单独开关；未配置的玩法默认拦截。
- max_tickets_per_match_play：同一比赛 ID + 玩法的注单数量上限。
- stake.min/max：金额边界，包含 50 和 500 两个边界值。
- enabled：总开关关闭时默认拦截。

## 在本地校验流程中调用

    from betting_rules import load_rules, evaluate_bet

    rules = load_rules("config/betting_rules.json")
    decision = evaluate_bet(
        rules,
        match_id="match-001",
        play="独赢",
        stake=100,
        existing_ticket_count=0,
    )
    if not decision.allowed:
        print(f"拦截：{decision.code} - {decision.message}")
    else:
        print("通过本地规则校验")

注单数量按比赛 ID 与玩法分别统计。调用方必须提供可信的已有注单数；本模块不维护订单账本，也不自动推断注单是否成功。

## 测试

    python -m unittest tests.test_betting_rules -v

配置缺失、JSON 格式错误或字段不合法时，load_rules() 会抛出 BettingRulesConfigError。请修复配置后重启，不要在配置异常时绕过校验。
