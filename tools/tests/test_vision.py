#!/usr/bin/env python3
"""Юнит-тесты зрения (vision.py): признаки, эталон, оценка, вердикты.

Запуск: python3 tools/tests/test_vision.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2] / "custom_components" / "rain_techlan"
sys.path.insert(0, str(ROOT))
import vision as V  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} {extra}")


def frame(brightness: int, gloss: bool = False, ripple: bool = False, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((120, 160, 3), brightness, dtype=np.uint8)
    if ripple:
        img[::2, :, :] = np.clip(brightness + 25, 0, 255)
    if gloss:
        img[40:60, 40:90, :] = 240
    img = np.clip(img.astype(np.int16) + rng.integers(-3, 4, img.shape), 0, 255).astype(np.uint8)
    return img


# --- режимы света (Белвью, UTC−7) ---
check("режим: 20:00 UTC = день", V.bucket_for(datetime(2026, 9, 24, 20, tzinfo=timezone.utc)) == "day")
check("режим: 02:00 UTC = сумерки", V.bucket_for(datetime(2026, 9, 25, 2, tzinfo=timezone.utc)) == "dusk")
check("режим: 12:00 UTC = сумерки (05:00 местного)", V.bucket_for(datetime(2026, 9, 24, 12, tzinfo=timezone.utc)) == "dusk")
check("режим: 09:00 UTC = ночь", V.bucket_for(datetime(2026, 9, 24, 9, tzinfo=timezone.utc)) == "night")

# --- признаки ---
f_dry = V.features(frame(90))
f_gloss = V.features(frame(90, gloss=True))
f_ripple = V.features(frame(90, ripple=True))
check("глянец растёт на бликах", f_gloss["gloss"] > f_dry["gloss"])
check("рябь растёт на полосах", f_ripple["ripple"] > f_dry["ripple"])
check("признаки: ключи на месте", set(f_dry) >= set(V.FEATURES))

# --- эталон и оценка ---
ZONE = {"id": "z1", "name": "Дорожка", "x": 0.1, "y": 0.6, "w": 0.6, "h": 0.35}
recs = [{"camera": "c", "zone": "z1", "bucket": "night", "dry": True,
         "feats": V.features(frame(90, seed=i))} for i in range(10)]
ref = V.build_reference(recs)
stat = ref["c|z1|night"]
check("эталон собрался", stat["mean"]["n"] == 10)
check("эталон: есть p05/p95", "p05" in stat["mean"] and "p95" in stat["mean"])

r_dry = V.score(V.features(frame(90, seed=99)), {"night": stat}, "night")
check("сухой кадр → score 0", r_dry["score"] == 0.0 and r_dry["verdict"] == "сухо", str(r_dry))

wet_feats = V.features(frame(90, gloss=True, ripple=True, seed=7))
# «замочим» сильно: блики почти везде
img_wet = frame(90, gloss=True, ripple=True)
img_wet[:] = np.clip(img_wet.astype(np.int16) + 30, 0, 255).astype(np.uint8)
img_wet[30:90, 20:140] = 245
r_wet = V.score(V.features(img_wet), {"night": stat}, "night")
check("мокрый кадр → дождь", r_wet["score"] >= V.VERDICT_WET and r_wet["verdict"] == "дождь", str(r_wet))

# --- фолбэк режима ---
r_fb = V.score(V.features(frame(90, seed=3)), {"night": stat}, "dusk")
check("фолбэк режима работает", r_fb["bucket_used"] == "night" and r_fb["fallback"] is True, str(r_fb))
r_none = V.score(V.features(frame(90)), None, "night")
check("без эталона — вердикт «нет эталона»", r_none["verdict"] == "нет эталона")

# --- evaluate/combine ---
res = V.evaluate(frame(90, seed=5), [ZONE], ref, "c", datetime(2026, 9, 24, 9, tzinfo=timezone.utc))
check("evaluate: зона + весь кадр", {r["zone"] for r in res} == {"_all", "z1"}, str([r["zone"] for r in res]))
check("combine: без дождя", V.combine(res)["verdict"] == "сухо")
check("is_wet: порог", V.is_wet({"score": 0.5}) and not V.is_wet({"score": 0.49}))

print(f"\n  ИТОГ: PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
