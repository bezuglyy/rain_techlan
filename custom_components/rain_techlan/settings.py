"""Настройки детектора осадков: камеры, зоны, порог, «истина», запреты, реакции.

Хранятся в options записи конфигурации HA и кэшируются в памяти, чтобы панель
могла менять их на лету без перезагрузки интеграции.
"""
from __future__ import annotations

import copy
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CAMERA_THRESHOLD,
    CONF_CAM_THRESHOLD,
    CONF_CAMERAS,
    CONF_INTERLOCKS,
    CONF_REACTIONS,
    CONF_TRUTH_ENTITY,
    CONF_ZONES,
    DEFAULT_REACTIONS,
    DOMAIN,
)
from .interlocks import normalize_interlocks

_LOGGER = logging.getLogger(__name__)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_zone(zone: dict[str, Any], index: int) -> dict[str, Any]:
    """Зона: доли 0..1, имя, привязка к камере (пусто = все камеры)."""
    def f(v, d):
        return min(1.0, max(0.0, _num(v, d)))

    x, y = f(zone.get("x"), 0.1), f(zone.get("y"), 0.6)
    w, h = f(zone.get("w"), 0.6) or 0.01, f(zone.get("h"), 0.3) or 0.01
    if x + w > 1.0:
        w = 1.0 - x
    if y + h > 1.0:
        h = 1.0 - y
    return {
        "id": str(zone.get("id") or f"z{index}"),
        "name": str(zone.get("name") or f"Зона {index}"),
        "camera": str(zone.get("camera") or ""),
        "x": round(x, 4), "y": round(y, 4),
        "w": round(max(w, 0.005), 4), "h": round(max(h, 0.005), 4),
    }


def normalize_camera(cam: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "id": str(cam.get("id") or f"cam{index}"),
        "name": str(cam.get("name") or f"Камера {index}"),
        "url": str(cam.get("url") or ""),
        "user": str(cam.get("user") or ""),
        "password": str(cam.get("password") or ""),
    }


class Settings:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.reload()

    def reload(self) -> None:
        merged = {**self.entry.data, **self.entry.options}
        self.cameras = [normalize_camera(c, i + 1) for i, c in enumerate(merged.get(CONF_CAMERAS) or []) if isinstance(c, dict)]
        self.zones = [normalize_zone(z, i + 1) for i, z in enumerate(merged.get(CONF_ZONES) or []) if isinstance(z, dict)]
        self.threshold = _num(merged.get(CONF_CAM_THRESHOLD), CAMERA_THRESHOLD)
        self.truth_entity = str(merged.get(CONF_TRUTH_ENTITY) or "")
        reactions = copy.deepcopy(DEFAULT_REACTIONS)
        for ev, cfg in (merged.get(CONF_REACTIONS) or {}).items():
            if ev in reactions and isinstance(cfg, dict):
                reactions[ev].update(cfg)
        self.reactions = reactions
        self.interlocks = normalize_interlocks(merged.get(CONF_INTERLOCKS))

    async def async_save(self, patch: dict[str, Any]) -> None:
        options = dict(self.entry.options)
        for key, value in patch.items():
            if key == CONF_CAMERAS and isinstance(value, list):
                options[CONF_CAMERAS] = [normalize_camera(c, i + 1) for i, c in enumerate(value) if isinstance(c, dict)]
            elif key == CONF_ZONES and isinstance(value, list):
                options[CONF_ZONES] = [normalize_zone(z, i + 1) for i, z in enumerate(value) if isinstance(z, dict)]
            elif key == CONF_INTERLOCKS and isinstance(value, list):
                options[CONF_INTERLOCKS] = normalize_interlocks(value)
            elif key == CONF_REACTIONS and isinstance(value, dict):
                reactions = copy.deepcopy(self.reactions)
                for ev, cfg in value.items():
                    if ev in reactions and isinstance(cfg, dict):
                        reactions[ev].update(cfg)
                options[CONF_REACTIONS] = reactions
            elif key == CONF_CAM_THRESHOLD:
                options[CONF_CAM_THRESHOLD] = _num(value, CAMERA_THRESHOLD)
            elif key == CONF_TRUTH_ENTITY:
                options[CONF_TRUTH_ENTITY] = str(value or "")
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        self.reload()

    def as_dict(self) -> dict[str, Any]:
        return {
            "cameras": self.cameras, "zones": self.zones, "threshold": self.threshold,
            "truth_entity": self.truth_entity, "reactions": self.reactions,
            "interlocks": self.interlocks,
        }


def get_settings(hass: HomeAssistant, entry: ConfigEntry) -> Settings:
    store = hass.data.setdefault(f"{DOMAIN}_store", {})
    key = f"settings:{entry.entry_id}"
    if key not in store:
        store[key] = Settings(hass, entry)
    return store[key]
