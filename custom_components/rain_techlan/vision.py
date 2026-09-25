"""Зрение для детектора осадков: признаки кадра, эталон «сухо», оценка.

Чистая логика без зависимостей от Home Assistant — тестируется отдельно.
Декодирование JPEG — Pillow (есть в HA), математика — numpy (есть в HA).

Признаки (по зоне и по всему кадру):
    mean    — средняя яркость (мокрое покрытие темнее днём)
    std     — разброс яркости
    gloss   — доля «блестящих» пикселей (яркость >= 235): плёнка воды даёт блики
    bright  — доля очень светлых пикселей (>= 200)
    sharp   — дисперсия лапласиана (резкость/структура: капли и потёки её повышают)
    ripple  — высокочастотная составляющая (высокие частоты: рябь от капель)
    sat     — средняя насыщенность (ночь/ИК ≈ 0, день > 0)

Световые режимы (по местному времени площадки):
    day 07–19 · dusk 05–07 и 19–21 · night 21–05
"""

from __future__ import annotations

import io
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import numpy as np
from PIL import Image

FEATURES = ("mean", "std", "gloss", "bright", "sharp", "ripple", "sat")
BUCKETS = ("day", "dusk", "night")
# Если для режима нет эталона — берём ближайший по «характеру света»
BUCKET_FALLBACK = {
    "day": ("dusk", "night"),
    "dusk": ("day", "night"),
    "night": ("dusk", "day"),
}

# Минимально значимое изменение признака (иначе шум, а не вода)
MIN_ABS = {"gloss": 0.004, "bright": 0.020, "ripple": 1.0, "sat": 2.0,
           "sharp_rel": 0.12, "mean_rel": 0.05, "std_rel": 0.05}

# Веса признаков в оценке (ночь — опора на блики/рябь, день — плюс затемнение)
WEIGHTS_NIGHT = {"gloss": 0.3, "ripple": 0.15, "sharp": 0.15, "mean_down": 0.4}
WEIGHTS_LIGHT = {"gloss": 0.35, "ripple": 0.2, "sharp": 0.1, "mean_down": 0.35}

VERDICT_WET = 0.5  # «дождь идёт»
VERDICT_MAYBE = 0.25  # «возможно»


def local_hour(ts: datetime, offset_hours: int = -7) -> int:
    """Местный час площадки (по умолчанию Белвью, UTC−7)."""
    return (ts.astimezone(timezone.utc) + timedelta(hours=offset_hours)).hour


def bucket_for(ts: datetime, offset_hours: int = -7) -> str:
    h = local_hour(ts, offset_hours)
    if 7 <= h < 19:
        return "day"
    if 5 <= h < 7 or 19 <= h < 21:
        return "dusk"
    return "night"


# ---------------------------------------------------------------- признаки
def decode(data: bytes) -> np.ndarray:
    """JPEG/PNG → RGB numpy (uint8)."""
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


def crop(img: np.ndarray, zone: dict[str, Any] | None) -> np.ndarray:
    """Вырезать зону (доли 0..1). Без зоны — весь кадр."""
    if not zone:
        return img
    h, w = img.shape[:2]
    x = int(max(0.0, min(1.0, float(zone.get("x", 0)))) * w)
    y = int(max(0.0, min(1.0, float(zone.get("y", 0)))) * h)
    zw = max(1, int(float(zone.get("w", 1)) * w))
    zh = max(1, int(float(zone.get("h", 1)) * h))
    part = img[y : min(h, y + zh), x : min(w, x + zw)]
    return part if part.size else img


def _laplacian(g: np.ndarray) -> np.ndarray:
    """Дискретный лапласиан 4-соседей (numpy, без OpenCV)."""
    out = np.zeros_like(g, dtype=np.float32)
    out[1:-1, 1:-1] = (
        -4.0 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    )
    return out


def _blur3(g: np.ndarray) -> np.ndarray:
    """Простое размытие 3×3 (усреднение) для оценки высоких частот."""
    p = np.pad(g, 1, mode="edge")
    return (
        p[:-2, :-2]
        + p[:-2, 1:-1]
        + p[:-2, 2:]
        + p[1:-1, :-2]
        + p[1:-1, 1:-1]
        + p[1:-1, 2:]
        + p[2:, :-2]
        + p[2:, 1:-1]
        + p[2:, 2:]
    ) / 9.0


def features(img: np.ndarray, zone: dict[str, Any] | None = None) -> dict[str, float]:
    """Признаки участка кадра."""
    part = crop(img, zone)
    if part.ndim != 3 or part.shape[0] < 3 or part.shape[1] < 3:
        return {k: 0.0 for k in FEATURES}
    a = part.astype(np.float32)
    g = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
    lap = _laplacian(g)
    blur = _blur3(g)
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1.0), 0.0)
    return {
        "mean": round(float(g.mean()), 2),
        "std": round(float(g.std()), 2),
        "gloss": round(float((g >= 235).mean()), 4),
        "bright": round(float((g >= 200).mean()), 4),
        "sharp": round(float(lap.var()), 1),
        "ripple": round(float((g - blur).std()), 2),
        "sat": round(float(sat.mean()) * 100.0, 1),
    }


# ---------------------------------------------------------------- эталон/оценка
def build_reference(
    records: Iterable[dict[str, Any]],
) -> dict[str, dict[str, dict[str, float]]]:
    """По сухим замерам построить эталон: ключ «камера:зона:режим» → метрики.

    records: элементы с полями camera, zone, bucket, feats (dict) и dry=True.
    """
    groups: dict[str, list[dict[str, float]]] = {}
    for r in records:
        if not r.get("dry"):
            continue
        key = f"{r.get('camera')}|{r.get('zone')}|{r.get('bucket')}"
        groups.setdefault(key, []).append(r.get("feats") or {})
    ref: dict[str, dict[str, dict[str, float]]] = {}
    for key, items in groups.items():
        stat: dict[str, dict[str, float]] = {}
        for m in FEATURES:
            vals = np.array([float(i.get(m, 0.0)) for i in items], dtype=np.float32)
            if vals.size == 0:
                continue
            stat[m] = {
                "median": round(float(np.median(vals)), 4),
                "p05": round(float(np.percentile(vals, 5)), 4),
                "p25": round(float(np.percentile(vals, 25)), 4),
                "p75": round(float(np.percentile(vals, 75)), 4),
                "p95": round(float(np.percentile(vals, 95)), 4),
                "n": int(vals.size),
            }
        if stat:
            ref[key] = stat
    return ref


def _need(m: str, stat: dict[str, float]) -> float:
    """Значимый порог для метрики: выход за границу ИЛИ абсолютный минимум."""
    med, hi, lo = stat["median"], stat["p95"], stat["p05"]
    if m == "mean":
        # затемнение — устойчивый сдвиг яркости: сравниваем с p25, а не с p05
        return max(med - stat.get("p25", lo), MIN_ABS["mean_rel"] * abs(med))
    if m == "std":
        return max(stat.get("p75", hi) - med, MIN_ABS["std_rel"] * abs(med))
    if m == "gloss":
        return max(hi - med, MIN_ABS["gloss"])
    if m == "bright":
        return max(hi - med, MIN_ABS["bright"])
    if m == "ripple":
        return max(hi - med, MIN_ABS["ripple"])
    if m == "sat":
        return max(hi - med, MIN_ABS["sat"])
    if m == "sharp":
        return max(hi - med, MIN_ABS["sharp_rel"] * abs(med))
    if m == "std":
        return max(hi - med, MIN_ABS["std_rel"] * abs(med))
    return max(hi - med, MIN_ABS["mean_rel"] * abs(med))


def _dev(
    feats: dict[str, float], stat: dict[str, float], m: str, up: bool = True
) -> float:
    """0 — в пределах нормы; 1.0 — ровно на значимом пороге; >1 — выше."""
    if not stat:
        return 0.0
    med = float(stat.get("median", 0.0))
    n = _need(m, stat)
    if n <= 0:
        return 0.0
    v = float(feats.get(m, 0.0))
    return (v - (med + n)) / n if up else ((med - n) - v) / n


def score(
    feats: dict[str, float], ref: dict[str, dict[str, float]] | None, bucket: str
) -> dict[str, Any]:
    """Оценка «дождливости» участка против эталона (0…1) + вердикт."""
    if not ref:
        return {
            "score": 0.0,
            "verdict": "нет эталона",
            "bucket": bucket,
            "bucket_used": None,
            "fallback": False,
            "dev": {},
        }
    used, stat = bucket, ref.get(bucket)
    if not stat:
        for fb in BUCKET_FALLBACK.get(bucket, ()):
            if ref.get(fb):
                used, stat = fb, ref[fb]
                break
    if not stat:
        return {
            "score": 0.0,
            "verdict": "нет эталона",
            "bucket": bucket,
            "bucket_used": None,
            "fallback": False,
            "dev": {},
        }
    gloss = _dev(feats, stat.get("gloss") or {}, "gloss")
    ripple = _dev(feats, stat.get("ripple") or {}, "ripple")
    sharp = _dev(feats, stat.get("sharp") or {}, "sharp")
    dark = _dev(feats, stat.get("mean") or {}, "mean", up=False)
    weights = WEIGHTS_NIGHT if used == "night" else WEIGHTS_LIGHT
    parts = {"gloss": gloss, "ripple": ripple, "sharp": sharp}
    if "mean_down" in weights:
        parts["mean_down"] = dark
    total = sum(weights[k] * max(0.0, min(1.0, parts.get(k, 0.0))) for k in weights)
    total = max(0.0, min(1.0, total))
    verdict = (
        "дождь"
        if total >= VERDICT_WET
        else ("возможно" if total >= VERDICT_MAYBE else "сухо")
    )
    return {
        "score": round(total, 3),
        "verdict": verdict,
        "bucket": bucket,
        "bucket_used": used,
        "fallback": used != bucket,
        "dev": {k: round(v, 2) for k, v in parts.items()},
    }


def evaluate(
    img: np.ndarray,
    zones: list[dict[str, Any]],
    ref: dict[str, dict[str, Any]],
    camera: str,
    ts: datetime,
    offset_hours: int = -7,
) -> list[dict[str, Any]]:
    """Оценка кадра по всем зонам камеры (участок «всё» — тоже зона с id '_all')."""
    b = bucket_for(ts, offset_hours)
    out = []
    for zone in [{"id": "_all", "name": "Весь кадр", **{}}] + list(zones or []):
        zid = str(zone.get("id") or "_all")
        f = features(img, None if zid == "_all" else zone)
        stat = ref.get(f"{camera}|{zid}|{b}")
        r = score(f, {b: stat} if stat else None, b)
        out.append(
            {
                "zone": zid,
                "name": zone.get("name") or zid,
                "camera": camera,
                "feats": f,
                **r,
            }
        )
    return out


def is_wet(result: dict[str, Any], threshold: float = VERDICT_WET) -> bool:
    return float(result.get("score") or 0.0) >= threshold


def combine(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Общая оценка по камерам/зонам: берём максимум (кроме служебной '_all')."""
    scored = [r for r in results if r.get("zone") != "_all" and r.get("bucket_used")]
    if not scored:
        scored = results
    if not scored:
        return {"score": 0.0, "verdict": "нет данных", "zone": None}
    best = max(scored, key=lambda r: float(r.get("score") or 0.0))
    return {
        "score": float(best.get("score") or 0.0),
        "verdict": best.get("verdict"),
        "zone": best.get("name") or best.get("zone"),
        "camera": best.get("camera"),
    }


# ---------------------------------------------------------------- временное сравнение
RECENT_N = 40           # сколько последних СУХИХ замеров держим как «свежий эталон»
TEMPORAL_MIN = 5        # меньше — доверяем только конверту


def temporal_score(cur: dict, history: list | None, bucket: str = "night") -> dict:
    """Сравнение с недавними СУХИМИ кадрами той же камеры/зоны.

    «Стало темнее / появились блики / рябь» = намокло. Ночью в ИК это основной признак.
    history: список пар (feats, dry) — последние замеры (dry=True попадают в эталон).
    """
    vals = [f for f, dry in (history or []) if dry]
    vals = vals[-RECENT_N:]
    if len(vals) < TEMPORAL_MIN:
        return {"score": 0.0, "n": len(vals)}
    base = {m: float(np.median([float(v.get(m, 0.0)) for v in vals])) for m in FEATURES}
    def _up(m: str, rel: float, absol: float = 0.0) -> float:
        b = base.get(m, 0.0); v = float(cur.get(m, 0.0))
        need = max(rel * abs(b), absol, 1e-6)
        return max(0.0, (v - b) / need)
    def _down(m: str, rel: float) -> float:
        b = base.get(m, 0.0); v = float(cur.get(m, 0.0))
        need = max(rel * abs(b), 1e-6)
        return max(0.0, (b - v) / need)
    parts = {"mean_down": _down("mean", 0.03), "gloss": _up("gloss", 0.5, 0.004),
             "ripple": _up("ripple", 0.15, 1.0)}
    w = ({"mean_down": 0.55, "gloss": 0.25, "ripple": 0.20} if bucket == "night"
         else {"mean_down": 0.45, "gloss": 0.35, "ripple": 0.20})
    score = sum(w[k] * min(1.0, parts[k]) for k in w)
    return {"score": round(max(0.0, min(1.0, score)), 3), "n": len(vals),
            "dev": {k: round(v, 2) for k, v in parts.items()},
            "base": {k: round(base[k], 2) for k in ("mean", "gloss", "ripple")},
            "cur": {k: round(float(cur.get(k, 0.0)), 2) for k in ("mean", "gloss", "ripple")}}
