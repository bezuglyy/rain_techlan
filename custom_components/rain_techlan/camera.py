"""Камера-детектор осадков для rain_techlan.

- берёт кадры с камер (HTTP-снимок, в т.ч. регистратор Devline/Line с Basic-авторизацией);
- считает признаки по зонам (vision.py), ведёт эталон «сухо» и оценку;
- копит замеры и журнал, умеет помечать «сухо» вручную и по «истине» (дождю);
- отдаёт общий вердикт и кадр с разметкой для панели.

«Истина» для автообучения эталона: любой бинарный сенсор дождя, доступный в HA
(например, наш `binary_sensor.osadki_belviu_dozhd_seichas` с ARM) — задаётся в настройках.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant

from . import vision
from .const import (
    SEASON_MIN_THRESHOLD,
    SEASON_RELAX_STEP,
    CAM_DRY_CONFIRM,
    CAM_WET_CONFIRM,
    CAMERA_MAX_SAMPLES,
    CAMERA_THRESHOLD,
    JOURNAL_LIMIT,
    SITE_UTC_OFFSET,
    STORE_DIR,
)

_LOGGER = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Detector:
    """Детектор осадков по камерам (одна инстанция на запись конфигурации)."""

    def __init__(
        self,
        hass: HomeAssistant,
        cameras: list[dict[str, Any]],
        zones: list[dict[str, Any]],
        truth_entity: str = "",
        threshold: float = CAMERA_THRESHOLD,
    ) -> None:
        self.hass = hass
        self.cameras = cameras or []
        self.zones = zones or []
        self.truth_entity = truth_entity
        self.threshold = float(threshold)

        self.dir = Path(hass.config.path(STORE_DIR))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.samples_path = self.dir / "samples.jsonl"
        self.reference_path = self.dir / "reference.json"
        self.journal_path = self.dir / "journal.jsonl"

        self.reference: dict[str, Any] = {}
        self._recent: dict[str, list] = {}
        self._wet_streak = 0
        self._sim_wet = None      # тест-режим «смоделировать дождь»
        self._dry_streak = 0
        self.last: dict[str, Any] = {}
        self.journal: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._samples_cached = 0
        self._loaded = False

    # ------------------------------------------------------------------ хранилище
    def _load(self) -> None:
        try:
            self.reference = json.loads(self.reference_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            self.reference = {}
        try:
            lines = self.journal_path.read_text(encoding="utf-8").splitlines()
            self.journal = [json.loads(x) for x in lines[-JOURNAL_LIMIT:] if x.strip()]
        except Exception:  # noqa: BLE001
            self.journal = []

    def samples(self) -> list[dict[str, Any]]:
        """Прочитать замеры (только из executor-потока!)."""
        out = []
        try:
            for line in self.samples_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            pass
        return out

    async def async_init(self) -> None:
        """Загрузить состояние (в executor, чтобы не блокировать цикл)."""
        await self.hass.async_add_executor_job(self._load)

    def _load_sync(self) -> None:
        self._load()

    async def _async_samples(self) -> list[dict[str, Any]]:
        return await self.hass.async_add_executor_job(self.samples)

    def _store_frame(self, camera: str, data: bytes) -> None:
        """Сохранить последний кадр (чистый — панели, размеченный — сущностям image)."""
        try:
            (self.dir / "last").mkdir(exist_ok=True)
            (self.dir / "last" / f"{camera}.jpg").write_bytes(data)
            # «чистый» кадр в статику панели: зоны панель рисует сама (иначе будут дубли)
            static = Path(__file__).parent / "frontend" / f"raw_{camera}.jpg"
            static.write_bytes(data)
        except Exception:  # noqa: BLE001
            pass

    def _append_samples(self, records: list[dict[str, Any]]) -> None:
        """Записать пачку замеров (вызывать в executor)."""
        if not records:
            return
        with open(self.samples_path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        lines = self.samples_path.read_text(encoding="utf-8").splitlines()
        if len(lines) > CAMERA_MAX_SAMPLES:
            self.samples_path.write_text(
                "\n".join(lines[-CAMERA_MAX_SAMPLES:]) + "\n", encoding="utf-8"
            )
        self._samples_cached = len(lines[-CAMERA_MAX_SAMPLES:])

    def _append_sample(self, record: dict[str, Any]) -> None:
        with open(self.samples_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        # подрезаем файл, чтобы не рос бесконечно
        lines = self.samples_path.read_text(encoding="utf-8").splitlines()
        if len(lines) > CAMERA_MAX_SAMPLES:
            self.samples_path.write_text(
                "\n".join(lines[-CAMERA_MAX_SAMPLES:]) + "\n", encoding="utf-8"
            )

    def add_journal(self, kind: str, text: str, **extra: Any) -> None:
        rec = {
            "ts": _now().isoformat(timespec="seconds"),
            "kind": kind,
            "text": text,
            **extra,
        }
        self.journal.append(rec)
        self.journal = self.journal[-JOURNAL_LIMIT:]
        with open(self.journal_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ эталон
    def rebuild_reference(self) -> dict[str, Any]:
        """Пересобрать эталон «сухо» по накопленным сухим замерам."""
        recs = []
        self._recent = {}
        for r in self.samples():
            _k = f"{r.get('camera')}|{r.get('zone')}"
            self._recent.setdefault(_k, []).append((r.get("feats") or {}, bool(r.get("dry"))))
            self._recent[_k] = self._recent[_k][-1500:]
            recs.append(
                {
                    "camera": r.get("camera"),
                    "zone": r.get("zone"),
                    "bucket": r.get("bucket"),
                    "dry": bool(r.get("dry")),
                    "feats": r.get("feats") or {},
                }
            )
        self.reference = vision.build_reference(recs)
        self.reference_path.write_text(
            json.dumps(self.reference, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self.reference

    def irrigation_running(self) -> bool:
        """Идёт ли полив прямо сейчас (для отличия дождя от спринклеров)."""
        try:
            for st in self.hass.states.async_all("binary_sensor"):
                if st.entity_id.endswith("oroshenie_any_zone_running") and st.state == "on":
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def truth_is_wet(self) -> bool:
        """Идёт ли дождь по «истине» (сенсор дождя/met.no в HA)."""
        if not self.truth_entity:
            return False
        st = self.hass.states.get(self.truth_entity)
        return bool(st and st.state == "on")

    # ------------------------------------------------------------------ кадры
    @staticmethod
    async def fetch_frame(session: aiohttp.ClientSession, cam: dict[str, Any]) -> bytes:
        """Скачать кадр камеры (HTTP-снимок, Basic-авторизация при необходимости)."""
        url = str(cam.get("url") or "")
        if not url:
            raise ValueError("не задан url камеры")
        auth = None
        user, password = cam.get("user"), cam.get("password")
        if user:
            # Только BasicAuth: aiohttp запрещает комбинировать его с заголовком Authorization
            auth = aiohttp.BasicAuth(str(user), str(password or ""))
        timeout = aiohttp.ClientTimeout(total=25)
        async with session.get(url, auth=auth, timeout=timeout) as resp:
            resp.raise_for_status()
            return await resp.read()

    async def async_scan(self, store_frames: bool = True) -> dict[str, Any]:
        """Один проход: снять кадры, посчитать признаки, оценить, записать замеры."""
        async with self._lock:
            ts = _now()
            bucket = vision.bucket_for(ts, SITE_UTC_OFFSET)
            truth_wet = self.truth_is_wet()
            if self._sim_wet is not None:      # тест-режим
                truth_wet = self._sim_wet
            results: list[dict[str, Any]] = []
            pending: list[dict[str, Any]] = []
            errors: list[str] = []
            async with aiohttp.ClientSession() as session:
                for cam in self.cameras:
                    cid = str(cam.get("id") or cam.get("name") or "cam")
                    try:
                        data = await self.fetch_frame(session, cam)
                    except Exception as err:  # noqa: BLE001
                        errors.append(f"{cid}: {type(err).__name__}: {err}")
                        continue
                    if store_frames:
                        await self.hass.async_add_executor_job(
                            self._store_frame, cid, data
                        )
                    try:
                        img = vision.decode(data)
                    except Exception as err:  # noqa: BLE001
                        errors.append(f"{cid}: кадр не прочитан ({err})")
                        continue
                    cam_zones = [
                        z
                        for z in self.zones
                        if not z.get("camera") or z.get("camera") == cid
                    ]
                    evaluated = vision.evaluate(
                        img, cam_zones, self.reference, cid, ts, SITE_UTC_OFFSET
                    )
                    for r in evaluated:
                        t = vision.temporal_score(r["feats"], self._recent.get(f"{cid}|{r['zone']}"), bucket)
                        r["temporal"] = t                 # диагностика (базис/текущее)
                        if t.get("score", 0.0) > (r.get("score") or 0.0):
                            r["score"] = t["score"]
                            r["verdict"] = ("дождь" if t["score"] >= vision.VERDICT_WET
                                            else "возможно" if t["score"] >= vision.VERDICT_MAYBE
                                            else r.get("verdict"))
                        # замер пишем всегда; в эталон «сухо» попадут только сухие
                        dry = (not truth_wet) and (
                            r.get("score") or 0.0
                        ) < vision.VERDICT_MAYBE
                        pending.append(
                            {
                                "ts": ts.isoformat(timespec="seconds"),
                                "camera": cid,
                                "zone": r["zone"],
                                "bucket": bucket,
                                "feats": r["feats"],
                                "score": r.get("score"),
                                "verdict": r.get("verdict"),
                                "truth_wet": truth_wet,
                                "dry": dry,
                            }
                        )
                        results.append(r)
            if results:
                await self.hass.async_add_executor_job(self._append_samples, pending)
                await self.hass.async_add_executor_job(self.rebuild_reference)
            summary = vision.combine(results)
            # сезонная адаптация: давно нет дождя → порог чуть мягче (в пределах безопасного)
            eff = self.threshold
            try:
                rows = self._recent
                last_wet_ts = None
                for _k, items in rows.items():
                    for feats, dry in items:
                        if not dry:
                            last_wet_ts = True
                            break
                if not last_wet_ts:
                    eff = max(SEASON_MIN_THRESHOLD, self.threshold - SEASON_RELAX_STEP)
            except Exception:  # noqa: BLE001
                pass
            self.effective_threshold = eff
            cam_wet = (
                summary.get("score", 0.0) >= eff
                and summary.get("verdict") == "дождь"
            )
            # камера может «видеть воду» от собственного полива — это не дождь
            irrigating = self.irrigation_running()
            if cam_wet and irrigating and not truth_wet:
                cam_wet = False
                summary = {**summary, "camera_wet_irrigation": True}
                self.add_journal("note", "Покрытие мокрое, но идёт полив — это не дождь")
            # гистерезис: одиночные всплески (фонарь, машина, смена режима) дождём не считаем
            camera_ok = bool(results) and not (len(self.cameras) and len(errors) >= len(self.cameras))
            if not camera_ok and not truth_wet:
                # камеры недоступны и «истина» молчит — состояние не меняем (fail-safe)
                wet = bool(self.last.get("wet"))
                self.last = {**self.last, "camera_ok": False}
                self.add_journal("error", f"Камеры недоступны ({len(errors)}): состояние не меняем")
                return self.last
            raw_wet = bool(cam_wet or truth_wet)
            if raw_wet:
                self._wet_streak += 1
                self._dry_streak = 0
            else:
                self._dry_streak += 1
                self._wet_streak = 0
            wet = bool(self.last.get("wet"))
            if truth_wet:
                wet = True                                    # «истина» (met.no) надёжна — сразу
            elif self._wet_streak >= CAM_WET_CONFIRM:
                wet = True
            elif self._dry_streak >= CAM_DRY_CONFIRM:
                wet = False
            summary = {**summary, "camera_wet": bool(cam_wet), "truth_wet": bool(truth_wet)}
            prev = bool(self.last.get("wet"))
            self.last = {
                "sources": {"camera": bool(cam_wet), "truth": bool(truth_wet)},
                "ts": ts.isoformat(timespec="seconds"),
                "bucket": bucket,
                "results": results,
                "summary": summary,
                "wet": wet,
                "truth_wet": truth_wet,
                "errors": errors,
                "zones": len(self.zones),
                "cameras": len(self.cameras),
            }
            # разметка кадра для панели/дашборда (и в статику панели)
            for cam in self.cameras:
                cid = str(cam.get("id") or cam.get("name") or "cam")
                try:
                    await self.hass.async_add_executor_job(self.annotated_frame, cid)
                except Exception:  # noqa: BLE001
                    pass
            if wet != prev:
                self.add_journal(
                    "rain" if wet else "dry",
                    f"{'Обнаружен дождь' if wet else 'Дождь прекратился'} "
                    f"(оценка {summary.get('score')}, зона «{summary.get('zone')}»)",
                )
            return self.last

    # ------------------------------------------------------------------ разметка кадра
    async def async_annotated_frame(self, camera: str) -> bytes | None:
        return await self.hass.async_add_executor_job(self.annotated_frame, camera)

    def annotated_frame(self, camera: str, dirty: bool = False) -> bytes | None:
        """Кадр камеры с прямоугольниками зон (JPEG). dirty=True — яркая разметка."""
        from PIL import Image, ImageDraw  # локальный импорт: нужен только здесь

        path = self.dir / "last" / f"{camera}.jpg"
        if not path.exists():
            return None
        try:
            img = Image.open(path).convert("RGB")
        except Exception:  # noqa: BLE001
            return None
        draw = ImageDraw.Draw(img)
        w, h = img.size
        for z in self.zones:
            if z.get("camera") and z.get("camera") != camera:
                continue
            x, y = int(float(z.get("x", 0)) * w), int(float(z.get("y", 0)) * h)
            zw, zh = int(float(z.get("w", 0)) * w), int(float(z.get("h", 0)) * h)
            ok = None
            for r in self.last.get("results") or []:
                if r.get("camera") == camera and r.get("zone") == z.get("id"):
                    ok = (r.get("score") or 0) >= self.threshold
            color = (255, 64, 64) if ok else (64, 200, 120)
            if ok is None:
                color = (200, 200, 60)
            draw.rectangle([x, y, x + zw, y + zh], outline=color, width=3)
            draw.text((x + 4, y + 4), str(z.get("name") or z.get("id")), fill=color)
        out = self.dir / f"annotated_{camera}.jpg"
        img.save(out, format="JPEG", quality=80)
        data = out.read_bytes()
        # копия в статику панели: отдаётся без авторизации (панель показывает кадр всегда)
        try:
            static = Path(__file__).parent / "frontend" / f"last_{camera}.jpg"
            static.write_bytes(data)
        except Exception:  # noqa: BLE001
            pass
        return data

    # ------------------------------------------------------------------ обучение
    async def async_mark_dry(self, hours: float = 24.0) -> int:
        return await self.hass.async_add_executor_job(self.mark_dry, hours)

    def mark_dry(self, hours: float = 24.0) -> int:
        """Пометить замеры за период как «сухо» (для эталона)."""
        limit = (_now() - timedelta(hours=float(hours))).isoformat()
        rows = self.samples()
        changed = 0
        for r in rows:
            if r.get("ts", "") >= limit and r.get("ts", "") >= limit:
                r["dry"] = True
                changed += 1
        with open(self.samples_path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        self.rebuild_reference()
        self.add_journal("learn", f"Помечено «сухо»: {changed} замеров за {hours} ч")
        return changed

    def _series(self, hours: int = 24, points: int = 48) -> list[dict[str, Any]]:
        """Компактный ряд оценок камеры за сутки (для мини-графика)."""
        out, step = [], max(1, int(len(self.samples()) / points) or 1)
        try:
            rows = self.samples()[-1000:]
            for i, r in enumerate(rows):
                if i % step:
                    continue
                out.append({"ts": r.get("ts"), "score": r.get("score"),
                            "camera": r.get("camera"), "truth": bool(r.get("truth_wet"))})
        except Exception:  # noqa: BLE001
            pass
        return out[-points:]

    def state(self) -> dict[str, Any]:
        """Текущее состояние для панели/сущностей."""
        return {
            "ts": self.last.get("ts"),
            "summary": self.last.get("summary")
            or {"score": 0.0, "verdict": "нет данных"},
            "wet": bool(self.last.get("wet")),
            "camera_wet": bool((self.last.get("summary") or {}).get("camera_wet")),
            "truth_wet": bool((self.last.get("summary") or {}).get("truth_wet")),
            "camera_ok": self.last.get("camera_ok", True),
            "irrigation": bool(self.last.get("irrigation")),
            "sources": self.last.get("sources") or {},
            "wet_streak": self.last.get("wet_streak"),
            "dry_streak": self.last.get("dry_streak"),
            "truth_wet": bool(self.last.get("truth_wet")),
            "bucket": self.last.get("bucket"),
            "wet_streak": self.last.get("wet_streak"),
            "dry_streak": self.last.get("dry_streak"),
            "results": self.last.get("results") or [],
            "errors": self.last.get("errors") or [],
            "cameras": self.cameras,
            "zones": self.zones,
            "reference_groups": len(self.reference),
            "samples": self._samples_cached,
            "threshold": self.threshold,
            "effective_threshold": getattr(self, "effective_threshold", self.threshold),
            "series": self._series(),
        }
