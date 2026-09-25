#!/usr/bin/env python3
"""Дневная калибровка детектора осадков по накопленным кадрам (ARM).

Сравнивает дневные кадры (местное 07–19, Белвью) «сухо» и «мокро» по меткам met.no
и печатает медианы признаков + рекомендованные пороги. Отчёт — в reports/.
Запуск: python3 tools/day-calibrate.py [--hours 24]
"""
from __future__ import annotations
import argparse, glob, json, os, pathlib, re, statistics as st, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "rain_techlan"))
import vision as V  # noqa: E402

DATA = pathlib.Path("/opt/weather")
REPORTS = pathlib.Path(__file__).resolve().parents[1] / "reports"
ZONES = {"0": {"x":0.05,"y":0.55,"w":0.65,"h":0.40}, "5": {"x":0.10,"y":0.60,"w":0.60,"h":0.35}}


def labels():
    out = []
    try:
        for line in (DATA / "weather.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line); out.append(r)
    except Exception:
        pass
    return out


def wet_at(ts_iso, labs):
    """Ближайшая метка met.no (в пределах 40 мин): идёт ли дождь."""
    best, dist = None, None
    for r in labs:
        d = abs((__import__("datetime").datetime.fromisoformat(r["ts"]) -
                 __import__("datetime").datetime.fromisoformat(ts_iso)).total_seconds())
        if dist is None or d < dist:
            best, dist = r, d
    return bool(best and best.get("rain_now")) if (dist or 9e9) <= 2400 else None


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--hours", type=int, default=24); a = ap.parse_args()
    labs = labels()
    buckets = {}
    for f in glob.glob(str(DATA / "dataset" / "*" / "*.jpg")):
        day, name = pathlib.Path(f).parent.name, os.path.basename(f)
        m = re.search(r"ch(\d)\.jpg$", name)
        if not m:
            continue
        hh, mm = int(name[:2]), int(name[2:4])
        if not (7 <= hh < 19):                      # только день по местному (UTC+10)
            continue
        iso = f"{day}T{hh-10 if hh >= 10 else hh+14}:{mm:02d}:00+00:00" if False else None
        # переводим местное (UTC+10) в UTC
        utc_h = (hh - 10) % 24
        utc_day = day if hh >= 10 else (__import__("datetime").date.fromisoformat(day) - __import__("datetime").timedelta(days=1)).isoformat()
        iso = f"{utc_day}T{utc_h:02d}:{mm:02d}:00+00:00"
        w = wet_at(iso, labs)
        if w is None:
            continue
        try:
            img = V.decode(pathlib.Path(f).read_bytes())
        except Exception:
            continue
        key = (m.group(1), "wet" if w else "dry")
        buckets.setdefault(key, []).append(V.features(img, ZONES.get(m.group(1))))
    lines = ["# Дневная калибровка детектора осадков", ""]
    for ch in sorted({k[0] for k in buckets}):
        d, w = buckets.get((ch, "dry"), []), buckets.get((ch, "wet"), [])
        lines.append(f"## Канал ch{ch}: сухо {len(d)}, мокро {len(w)}")
        if not d or not w:
            lines.append("_недостаточно данных (нужны и сухие, и мокрые дневные кадры)_\n")
            continue
        lines.append("| признак | сухо (медиана) | мокро (медиана) | Δ |")
        lines.append("|---|---|---|---|")
        for m in ("mean", "std", "gloss", "sharp", "ripple"):
            a1 = st.median(x[m] for x in d); a2 = st.median(x[m] for x in w)
            lines.append(f"| {m} | {a1:.3f} | {a2:.3f} | {(a2-a1)/abs(a1)*100 if a1 else 0:+.1f}% |")
        lines.append("")
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"day-calibration-{__import__('datetime').date.today().isoformat()}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print("  отчёт:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
