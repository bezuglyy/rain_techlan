"""Кадры камер с разметкой зон — нативная сущность image (доступна через image_proxy)."""
from __future__ import annotations

import logging

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _detector(hass: HomeAssistant, entry: ConfigEntry):
    return hass.data.get(f"{DOMAIN}_store", {}).get(f"detector:{entry.entry_id}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    det = _detector(hass, entry)
    cams = list((det.cameras if det else []) or [])
    if not cams:  # камеры могли быть добавлены позже — дадим сущности при первой настройке
        return
    async_add_entities([RainCameraImage(hass, entry, c["id"], c.get("name") or c["id"]) for c in cams])


class RainCameraImage(ImageEntity):
    """Кадр камеры с прямоугольниками зон и цветом оценки."""

    _attr_has_entity_name = True
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, camera: str, name: str) -> None:
        super().__init__(hass)
        self.entry = entry
        self._satellite_id = entry.data.get("satellite_id", "")
        self._cam = camera
        self._attr_name = f"Кадр: {name}"
        self._attr_unique_id = f"{entry.entry_id}_frame_{camera}"
        self._attr_icon = "mdi:image-frame"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, str(getattr(self, "_satellite_id", "")))},
                          manufacturer="Techlan", model="Камера-детектор осадков")

    async def async_image(self) -> bytes | None:
        det = _detector(self.hass, self.entry)
        if not det:
            return None
        return await det.async_annotated_frame(self._cam)
