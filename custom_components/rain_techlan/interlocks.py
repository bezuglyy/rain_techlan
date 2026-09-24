"""Запреты (interlocks) для полива — «как у ворот»: правило по сенсору.

Правило: {name, entity_id, attribute (пусто=state), op, value, mode (block|warn),
enabled, actions (какие команды запрещаем: start_zone|start_program|all)}.

Проверка идёт ПЕРЕД командой полива; при запрете команда отклоняется
(ServiceValidationError), пишется событие `rain_techlan_interlock` и запись в журнал.
Недоступный сенсор запретом НЕ считается (fail-open, чтобы не блокировать полив зря).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # импорт только для типов: модуль должен грузиться и без HA (тесты)
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

OPS = ("above", "below", "equal", "not_equal", "is_on", "is_off")
MODES = ("block", "warn")
ACTIONS = ("start_zone", "start_program", "all")
MAX_RULES = 20

_OP_TEXT = {
    "above": ">",
    "below": "<",
    "equal": "=",
    "not_equal": "≠",
    "is_on": "включён",
    "is_off": "выключен",
}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_interlocks(raw: Any) -> list[dict[str, Any]]:
    """Привести список правил к корректному виду."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw[:MAX_RULES]):
        if not isinstance(item, dict) or not item.get("entity_id"):
            continue
        op = str(item.get("op") or "above")
        if op not in OPS:
            op = "above"
        mode = str(item.get("mode") or "block")
        if mode not in MODES:
            mode = "block"
        actions = [a for a in (item.get("actions") or ["all"]) if a in ACTIONS] or [
            "all"
        ]
        out.append(
            {
                "id": str(item.get("id") or f"i{i + 1}"),
                "name": str(item.get("name") or f"Запрет {i + 1}"),
                "entity_id": str(item["entity_id"]),
                "attribute": str(item.get("attribute") or ""),
                "op": op,
                "value": item.get("value", 0),
                "mode": mode,
                "enabled": bool(item.get("enabled", True)),
                "actions": actions,
            }
        )
    return out


def _state_value(hass: HomeAssistant, rule: dict[str, Any]) -> tuple[str, Any]:
    st = hass.states.get(rule["entity_id"])
    if st is None:
        return "unavailable", None
    attr = rule.get("attribute") or ""
    if attr:
        return st.state, st.attributes.get(attr)
    return st.state, st.state


def condition_met(hass: HomeAssistant, rule: dict[str, Any]) -> bool | None:
    """True — условие выполнено; False — нет; None — сенсор недоступен."""
    state, value = _state_value(hass, rule)
    if state == "unavailable" or state == "unknown" or value is None:
        return None
    op = rule["op"]
    if op == "is_on":
        return state in ("on", "true", "1", "open", "wet")
    if op == "is_off":
        return state in ("off", "false", "0", "closed", "dry")
    num, ref = _num(value), _num(rule.get("value"))
    if num is None or ref is None:
        if op == "equal":
            return str(value) == str(rule.get("value"))
        if op == "not_equal":
            return str(value) != str(rule.get("value"))
        return None
    if op == "above":
        return num > ref
    if op == "below":
        return num < ref
    if op == "equal":
        return abs(num - ref) < 1e-9
    if op == "not_equal":
        return abs(num - ref) >= 1e-9
    return None


def rule_applies(rule: dict[str, Any], action: str) -> bool:
    if action in (None, "", "all"):
        return True
    return "all" in rule["actions"] or action in rule["actions"]


def describe(rule: dict[str, Any]) -> str:
    st = rule["entity_id"]
    attr = f" → {rule['attribute']}" if rule.get("attribute") else ""
    return f"{rule['name']}: {st}{attr} {_OP_TEXT.get(rule['op'], rule['op'])} {rule.get('value')}"


def evaluate(
    hass: HomeAssistant, rules: list[dict[str, Any]], action: str = "all"
) -> dict[str, Any]:
    """Итог по всем правилам для действия: block/warn + тексты."""
    active: list[dict[str, Any]] = []
    for rule in rules or []:
        if not rule.get("enabled") or not rule_applies(rule, action):
            continue
        met = condition_met(hass, rule)
        if met is True:
            active.append(rule)
    blockers = [r for r in active if r["mode"] == "block"]
    return {
        "blocked": bool(blockers),
        "active": active,
        "blockers": blockers,
        "active_text": "; ".join(describe(r) for r in active),
        "block_text": "; ".join(describe(r) for r in blockers),
    }


def active_for_actions(
    hass: HomeAssistant, rules: list[dict[str, Any]]
) -> dict[str, Any]:
    """Сводка по всем действиям (для панели)."""
    out = {}
    for action in ACTIONS:
        out[action] = evaluate(hass, rules, action)
    return out
