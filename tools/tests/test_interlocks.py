#!/usr/bin/env python3
"""Юнит-тесты запретов (interlocks.py) на фальшивом hass."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "custom_components" / "rain_techlan"
sys.path.insert(0, str(ROOT))
import interlocks as IL  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} {extra}")


class St:
    def __init__(self, state, **attrs):
        self.state = state
        self.attributes = attrs


class Hass:
    def __init__(self, states):
        self.states = self

    def get(self, eid):
        return STATES.get(eid)


STATES = {
    "sensor.temp": St("4.2", unit="C"),
    "binary_sensor.rain": St("on"),
    "binary_sensor.dry": St("off"),
    "sensor.missing": None,
}
hass = Hass(STATES)

rules = IL.normalize_interlocks([
    {"name": "Дождь", "entity_id": "binary_sensor.rain", "op": "is_on", "mode": "block", "actions": ["all"]},
    {"name": "Мороз", "entity_id": "sensor.temp", "op": "below", "value": 5, "mode": "block", "actions": ["start_zone"]},
    {"name": "Тепло", "entity_id": "sensor.temp", "op": "above", "value": 30, "mode": "warn", "actions": ["all"]},
    {"name": "Нет сенсора", "entity_id": "sensor.missing", "op": "is_on", "mode": "block", "actions": ["all"]},
    {"name": "Выключено", "entity_id": "binary_sensor.rain", "op": "is_on", "mode": "block", "enabled": False},
])
check("нормализация: 5 правил", len(rules) == 5)
check("значение по умолчанию: mode=block", IL.normalize_interlocks([{"entity_id": "x"}])[0]["mode"] == "block")
check("некорректный op → above", IL.normalize_interlocks([{"entity_id": "x", "op": "bogus"}])[0]["op"] == "above")

check("условие is_on (дождь) выполнено", IL.condition_met(hass, rules[0]) is True)
check("условие below (мороз 4.2<5) выполнено", IL.condition_met(hass, rules[1]) is True)
check("условие above (30) не выполнено", IL.condition_met(hass, rules[2]) is False)
check("недоступный сенсор → None (не блокирует)", IL.condition_met(hass, rules[3]) is None)

ev_all = IL.evaluate(hass, rules, "all")
check("блок для all: есть", ev_all["blocked"] is True, str(ev_all["block_text"]))
check("блокировщики: дождь + мороз(только зона)", len(ev_all["blockers"]) == 2, str([r["name"] for r in ev_all["blockers"]]))
ev_prog = IL.evaluate(hass, rules, "start_program")
check("для start_program мороз не применяется", len(ev_prog["blockers"]) == 1)
check("warn-правило не блокирует", all(r["mode"] == "block" for r in ev_prog["blockers"]))
check("выключенное правило пропущено", all(r["name"] != "Выключено" for r in ev_all["active"]))
check("текст запрета читаемый", "Дождь" in ev_all["block_text"], ev_all["block_text"])

print(f"\n  ИТОГ: PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
