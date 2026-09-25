"""Числовые параметры: задержка дождя (дни) и время работы зон (минуты)."""
from __future__ import annotations

import logging
import re

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_SATELLITE_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)
CONF_ZONE_RUNTIMES = "zone_runtimes"


def _zone_title(station: dict) -> str:
    name = str(station.get("name") or "")
    m = re.search(r"(\d+)\s*$", name)
    if m:
        return f"Зона {int(m.group(1))}"
    return name or f"Зона {station.get('terminal') or station.get('id')}"


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
    # задержку дождя показывает штатный сенсор (sensor.oroshenie_rain_delay),
    # а меняется сервисом rain_techlan.set_rain_delay — дублирующее число не создаём
    entities = []
    entities += [ZoneRuntimeNumber(hass, entry, s, satellite_id) for s in stations if s.get("id")]
    async_add_entities(entities)


class RainDelayNumber(NumberEntity):
    """Задержка полива из-за дождя, дней."""

    _attr_has_entity_name = True
    _attr_should_poll = True          # значение читаем из координатора (облако)
    _attr_name = "Задержка дождя"
    _attr_icon = "mdi:weather-rainy"
    _attr_native_min_value = 0
    _attr_native_max_value = 14
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, hass, entry, api, satellite_id: int) -> None:
        self.hass = hass
        self.entry = entry
        self.api = api
        self._sat = satellite_id
        self._satellite_id = satellite_id
        self._attr_unique_id = f"{entry.entry_id}_rain_delay_days"
        self._value = float(entry.options.get("rain_delay_days") or 0)

    @property
    def device_info(self) -> DeviceInfo:
        # то же устройство, что и у облачных сущностей (контроллер) — без дублей
        return DeviceInfo(identifiers={(DOMAIN, str(getattr(self, "_satellite_id", "")))})

    @property
    def native_value(self) -> float:
        """Значение из облака: ищем координатор записи с satellite.rainDelay; иначе — штатный сенсор."""
        try:
            store = (self.hass.data.get(DOMAIN) or {}).get(self.entry.entry_id) or {}
            for coord in store.values():
                data = getattr(coord, "data", None)
                if isinstance(data, dict):
                    sat = data.get("satellite")
                    if isinstance(sat, dict) and sat.get("rainDelay") is not None:
                        return float(sat["rainDelay"])
        except Exception:  # noqa: BLE001
            pass
        # запасной вариант: значение штатного сенсора задержки (он тянет то же облако)
        for st in self.hass.states.async_all("sensor"):
            if st.entity_id.endswith("oroshenie_rain_delay") and st.state not in ("unknown", "unavailable"):
                try:
                    return float(st.state)
                except ValueError:
                    break
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        days = int(value)
        await self.hass.async_add_executor_job(self.api.set_rain_delay, self._sat, days)
        self._value = float(days)
        opts = dict(self.entry.options); opts["rain_delay_days"] = days
        self.hass.config_entries.async_update_entry(self.entry, options=opts)
        try:
            rt = ((self.hass.data.get(DOMAIN) or {}).get(self.entry.entry_id) or {}).get("realtime")
            if rt:
                await rt.async_request_refresh()
        except Exception:  # noqa: BLE001
            pass
        self.async_write_ha_state()


class ZoneRuntimeNumber(NumberEntity):
    """Сколько минут работает зона при включении (хранится в настройках)."""

    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_icon = "mdi:timer-outline"
    _attr_native_min_value = 1
    _attr_native_max_value = 240
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, hass, entry, station: dict, satellite_id: int) -> None:
        self.hass = hass
        self.entry = entry
        self._satellite_id = satellite_id
        self._id = int(station["id"])
        self._attr_name = f"{_zone_title(station)}: время работы, мин"
        self._attr_unique_id = f"{entry.entry_id}_runtime_{self._id}"

    @property
    def device_info(self) -> DeviceInfo:
        # то же устройство, что и у облачных сущностей (контроллер) — без дублей
        return DeviceInfo(identifiers={(DOMAIN, str(getattr(self, "_satellite_id", "")))})

    @property
    def native_value(self) -> float:
        return float((self.entry.options.get(CONF_ZONE_RUNTIMES) or {}).get(str(self._id), 5))

    async def async_set_native_value(self, value: float) -> None:
        opts = dict(self.entry.options)
        runtimes = dict(opts.get(CONF_ZONE_RUNTIMES) or {})
        runtimes[str(self._id)] = int(value)
        opts[CONF_ZONE_RUNTIMES] = runtimes
        self.hass.config_entries.async_update_entry(self.entry, options=opts)
        self.async_write_ha_state()
