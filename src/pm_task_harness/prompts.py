from __future__ import annotations

import json
from pathlib import Path

from .models import Task
from .report_policy import REPORT_ONLY_CONTRACT


OUTPUT_SCHEMA = {
    "decision": "buy_yes | buy_no | skip",
    "outcome": "Yes | No",
    "token_id": "chosen Polymarket CLOB token id, empty only when skip",
    "ai_prob": "number 0..1 for chosen outcome",
    "market_prob": "number 0..1 for chosen outcome",
    "edge": "ai_prob - market_prob",
    "confidence": "low | medium | high",
    "thesis": "short executable rationale",
    "definition": {
        "resolution_rule_summary": "how the market resolves",
        "decision_standard": "what must happen for Yes",
    },
    "evidence_table": [
        {
            "step": "base | evidence | final",
            "summary": "evidence or update",
            "source_url": "url or empty for base/final",
            "direction": "up | down | neutral",
            "strength": "weak | medium | strong",
            "prob_after": "number 0..1",
        }
    ],
    "sources": [{"title": "source title", "url": "https://...", "key_point": "short note"}],
}

FAIR_VALUE_SCHEMA = {
    "yes_prob": "number 0..1, current fair probability that the market resolves Yes",
    "confidence": "low | medium | high",
    "thesis": "short rationale for the current fair probability",
    "definition": {
        "resolution_rule_summary": "how the market resolves",
        "decision_standard": "what must happen for Yes",
    },
    "evidence_table": [
        {
            "step": "base | evidence | final",
            "summary": "evidence or update",
            "source_url": "url or empty for base/final",
            "direction": "up | down | neutral",
            "strength": "weak | medium | strong",
            "prob_after": "number 0..1",
        }
    ],
    "sources": [{"title": "source title", "url": "https://...", "key_point": "short note"}],
}


def build_task_prompt(task: Task, skill_path: Path) -> str:
    skill_text = skill_path.read_text(encoding="utf-8")
    market = task.market
    # Keep the prompt self-contained. Agent sessions should not need to infer
    # market metadata from surrounding files or previous tasks.
    market_payload = {
        "event_slug": market.event_slug,
        "market_slug": market.market_slug,
        "question": market.question,
        "description": market.description,
        "url": market.url,
        "outcomes": market.outcomes,
        "outcome_prices": market.outcome_prices,
        "clob_token_ids": market.clob_token_ids,
        "liquidity": market.liquidity,
        "volume_24hr": market.volume_24hr,
        "end_date": market.end_date,
        "category": market.category,
        "tags": market.tags,
    }
    goal = {
        "task_id": task.task_id,
        "objective": "Research exactly one Polymarket market and return an executable JSON decision.",
        "completion_standard": task.completion_standard,
        "static_filter_reasons": task.reasons,
    }
    # The slash goal helps CLIs that support explicit goals; the JSON # GOAL is
    # kept as a stable machine-readable contract for providers that ignore it.
    slash_goal = (
        "/goal Research exactly one selected Polymarket market and return one executable strict JSON "
        f"decision for task_id={task.task_id}. Completion standard: include "
        "decision/outcome/token_id/ai_prob/market_prob/edge/confidence/definition/"
        "evidence_table/sources; decision must be buy_yes, buy_no, or skip; buy only if "
        "the chosen outcome has at least 8 percentage points of edge and sources support it."
    )
    return "\n".join(
        [
            slash_goal,
            "",
            "# GOAL",
            json.dumps(goal, ensure_ascii=False, indent=2),
            "",
            "# SKILL",
            skill_text,
            "",
            "# MARKET_TASK",
            json.dumps(market_payload, ensure_ascii=False, indent=2),
            "",
            "# REQUIRED_OUTPUT_SCHEMA",
            json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2),
            "",
            "# HARD REQUIREMENTS",
            "- Output only one JSON object. No markdown fences.",
            "- Do not recommend a market other than MARKET_TASK.",
            "- Define the task using the market resolution standard before estimating probability.",
            "- Compare the chosen outcome probability against the corresponding Polymarket outcome price.",
            "- If evidence is weak or edge is below the threshold, return decision=skip.",
            "- For buy_yes use the Yes token_id. For buy_no use the No token_id.",
        ]
    )


def build_repair_prompt(previous_output: str, errors: list[str]) -> str:
    return "\n".join(
        [
            "/goal Repair the previous answer inside the existing task session until it satisfies the static executable JSON validator.",
            "",
            "Your previous answer failed static validation.",
            "Repair it in the SAME task context. Do not redo unrelated research unless needed.",
            "Return only one strict JSON object.",
            "",
            "Validation errors:",
            json.dumps(errors, ensure_ascii=False, indent=2),
            "",
            "Previous output:",
            previous_output,
        ]
    )


def build_fair_value_prompt(task: Task, skill_path: Path, purpose: str) -> str:
    skill_text = skill_path.read_text(encoding="utf-8")
    market = task.market
    market_payload = {
        "event_slug": market.event_slug,
        "market_slug": market.market_slug,
        "question": market.question,
        "description": market.description,
        "url": market.url,
        "outcomes": market.outcomes,
        "outcome_prices": market.outcome_prices,
        "clob_token_ids": market.clob_token_ids,
        "liquidity": market.liquidity,
        "volume_24hr": market.volume_24hr,
        "end_date": market.end_date,
        "category": market.category,
        "tags": market.tags,
    }
    slash_goal = (
        "/goal Estimate the current fair Yes probability for exactly one Polymarket market. "
        f"Purpose={purpose}; task_id={task.task_id}. Return only strict JSON; do not decide "
        "position size, buy/sell action, or Kelly sizing."
    )
    return "\n".join(
        [
            slash_goal,
            "",
            "# GOAL",
            json.dumps(
                {
                    "task_id": task.task_id,
                    "objective": "Return the current fair Yes probability; harness handles all trading math.",
                    "purpose": purpose,
                },
                ensure_ascii=False,
                indent=2,
            ),
            "",
            "# SKILL",
            skill_text,
            "",
            "# MARKET_TASK",
            json.dumps(market_payload, ensure_ascii=False, indent=2),
            "",
            "# REQUIRED_OUTPUT_SCHEMA",
            json.dumps(FAIR_VALUE_SCHEMA, ensure_ascii=False, indent=2),
            "",
            "# HARD REQUIREMENTS",
            "- Output only one JSON object. No markdown fences.",
            "- Estimate yes_prob using the market resolution standard, not your own definition.",
            "- Do not output a trade decision, position size, Kelly fraction, or token_id.",
            "- Include enough evidence for the harness to audit whether probability changed.",
        ]
    )


def build_chinese_report_prompt(
    *,
    position_address: str,
    positions: list[dict],
    new_tasks: list[Task],
    skill_path: Path,
) -> str:
    skill_text = skill_path.read_text(encoding="utf-8")
    market_payload = [_report_task_payload(task) for task in new_tasks]
    return "\n".join(
        [
            "/goal 只读分析 Polymarket 真实持仓和候选新盘口，只调用本次上下文中的证据，输出一份中文 Markdown 操作报告。",
            "",
            "# REPORT_ONLY_CONTRACT",
            json.dumps(REPORT_ONLY_CONTRACT, ensure_ascii=False, indent=2),
            "",
            "# SKILL",
            skill_text,
            "",
            "# POSITION_ADDRESS",
            position_address,
            "",
            "# CURRENT_POSITIONS_FROM_PUBLIC_API",
            json.dumps(positions, ensure_ascii=False, indent=2),
            "",
            "# NEW_CANDIDATE_MARKETS",
            json.dumps(market_payload, ensure_ascii=False, indent=2),
            "",
            "# HARD REQUIREMENTS",
            "- 输出中文 Markdown，不要输出 JSON。",
            "- 报告开头必须是一级标题：# 操作建议。",
            "- 开头必须简明扼要列出：我应该如何操作、盘口链接、该盘口是已有持仓还是新建仓、买/卖方向、数量或金额。",
            "- 如果没有足够证据或没有合适盘口，开头也必须明确写：暂不操作。",
            "- 只读分析，不要要求私钥、助记词、钱包验证码、交易 API secret 或任何能交易的权限。",
            "- 不要声称已经下单、撤单、转账、授权或执行了任何交易。",
            "- 后续正文必须包含：持仓同步摘要、候选盘口证据、概率判断、风险和反证、为什么这个操作比不操作更好。",
            "- 使用 MARKET url 字段给出可点击的 Polymarket 盘口链接。",
        ]
    )


def _report_task_payload(task: Task) -> dict:
    market = task.market
    return {
        "task_id": task.task_id,
        "score": task.score,
        "market_type": task.market_type,
        "filter_reasons": task.reasons,
        "market": {
            "event_slug": market.event_slug,
            "market_slug": market.market_slug,
            "question": market.question,
            "description": market.description,
            "url": market.url,
            "outcomes": market.outcomes,
            "outcome_prices": market.outcome_prices,
            "clob_token_ids": market.clob_token_ids,
            "liquidity": market.liquidity,
            "volume_24hr": market.volume_24hr,
            "end_date": market.end_date,
            "category": market.category,
            "tags": market.tags,
        },
    }
