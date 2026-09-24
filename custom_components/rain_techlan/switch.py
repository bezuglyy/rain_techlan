"""Переключатели: зоны полива (станции) и питание контроллера."""
from __future__ import annotations

import logging
import re
import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_SATELLITE_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)
CONF_ZONE_RUNTIMES = "zone_runtimes"


def _zone_title(station: dict) -> str:
    """«Зона 1» из «Station 001»."""
    name = str(station.get("name") or "")
    m = re.search(r"(\d+)\s*$", name)
    if m:
        return f"Зона {int(m.group(1))}"
    return name or f"Зона {station.get('terminal') or station.get('id')}"


def _runtimes(entry: ConfigEntry) -> dict[str, int]:
    return dict(entry.options.get(CONF_ZONE_RUNTIMES) or {})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    satellite_id = int(entry.data[CONF_SATELLITE_ID])
    try:
        stations = await hass.async_add_executor_job(api.get_station_list, satellite_id)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("rain_techlan: список зон не получен: %s", err)
        stations = []
    entities = [RainZoneSwitch(hass, entry, api, s, satellite_id) for s in stations if s.get("id")]
    entities.append(RainPowerSwitch(hass, entry, api, satellite_id))
    async_add_entities(entities)


class _Base(SwitchEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api) -> None:
        self.hass = hass
        self.entry = entry
        self.api = api

    @property
    def device_info(self) -> DeviceInfo:
        # то же устройство, что и у облачных сущностей (контроллер) — без дублей
        return DeviceInfo(identifiers={(DOMAIN, str(getattr(self, "_satellite_id", "")))})


class RainZoneSwitch(_Base):
    """Зона полива: включение = запуск зоны на её время работы."""

    def __init__(self, hass, entry, api, station: dict, satellite_id: int) -> None:
        super().__init__(hass, entry, api)
        self._satellite_id = satellite_id
        self._id = int(station["id"])
        self._name = _zone_title(station)
        self._attr_name = f"{self._name}: полив"
        self._attr_unique_id = f"{entry.entry_id}_zone_{self._id}"
        self._attr_icon = "mdi:sprinkler"
        self._until = 0.0

    @property
    def runtime_min(self) -> int:
        return int(_runtimes(self.entry).get(str(self._id), 5))

    @property
    def is_on(self) -> bool:
        return time.time() < self._until

    @property
    def extra_state_attributes(self) -> dict:
        return {"zone_id": self._id, "runtime_min": self.runtime_min}

    async def async_turn_on(self, **kwargs) -> None:
        minutes = self.runtime_min
        await self.hass.async_add_executor_job(self.api.start_station, self._id, minutes * 60)
        self._until = time.time() + minutes * 60
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(self.api.stop_station, self._id)
        self._until = 0.0
        self.async_write_ha_state()


class RainPowerSwitch(_Base):
    """Питание контроллера (On/Auto ↔ Off)."""

    def __init__(self, hass, entry, api, satellite_id: int) -> None:
        super().__init__(hass, entry, api)
        self._satellite_id = satellite_id
        self._sat = satellite_id
        self._attr_name = "Питание контроллера"
        self._attr_unique_id = f"{entry.entry_id}_power"
        self._attr_icon = "mdi:power"
        self._on = True

    @property
    def is_on(self) -> bool:
        return self._on

    async def async_turn_on(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(self.api.set_power, self._sat, True)
        self._on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(self.api.set_power, self._sat, False)
        self._on = False
        self.async_write_ha_state()
