"""Techlan Полив binary sensor platform."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RainBirdCoordinator, RainBirdConfigCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Techlan Полив binary sensors from a config entry."""
    coordinators       = hass.data[DOMAIN][entry.entry_id]
    config_coordinator = coordinators["config"]

    entities = [
        RainBirdConnectionBinarySensor(coordinators["realtime"], config_coordinator),
        RainBirdForecastBinarySensor(config_coordinator),
        RainBirdAnyZoneRunningBinarySensor(coordinators["realtime"], config_coordinator),
    ]

    for sensor in config_coordinator.data.get("sensors", []):
        if sensor.get("type", -1) != -1:
            entities.append(RainBirdRainSensor(config_coordinator, sensor))

    # Controllers reporting isMQTT expose the state of their local sensor
    # (SEN) terminals through AppSync; the REST sensor list does not, so this
    # is the only entity that tracks them. Created regardless of whether a
    # sensor is declared in IQ4: one with the factory jumper simply reads dry,
    # which is electrically correct.
    #
    # Decided from isMQTT, a hardware property from the REST API, rather than
    # from whether the first AppSync poll happened to succeed. Keying it on
    # that poll meant a single transient failure while Home Assistant started
    # hid the entity until the next restart, with nothing in the log to say
    # why. A failed poll now shows as unavailable instead.
    device_info = await hass.async_add_executor_job(
        coordinators["api"].get_device_info,
        coordinators["realtime"].satellite_id,
    )
    if device_info.get("isMQTT") and device_info.get("deviceUUID"):
        entities.append(
            RainBirdLocalSensorBinarySensor(
                coordinators["realtime"], config_coordinator
            )
        )

    entities.append(RainTechlanCameraRain(hass, entry))
    async_add_entities(entities)


class RainBirdConnectionBinarySensor(BinarySensorEntity):
    """Binary sensor reporting whether the controller is connected — real-time."""

    def __init__(
        self,
        coordinator: RainBirdCoordinator,
        config_coordinator: RainBirdConfigCoordinator,
    ) -> None:
        self._coordinator = coordinator
        self._config_coordinator = config_coordinator
        satellite = config_coordinator.data.get("satellite", {}) if config_coordinator.data else {}
        self._satellite_id = coordinator.satellite_id
        self._satellite_name = satellite.get("name", "Techlan Полив")
        self._attr_unique_id = f"{self._satellite_id}_connected"
        self._attr_name = "Связь с контроллером"
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_icon = "mdi:cloud-check"

    @property
    def device_info(self) -> DeviceInfo:
        satellite = self._config_coordinator.data.get("satellite", {}) if self._config_coordinator.data else {}
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._satellite_id))},
            name=self._satellite_name,
            manufacturer="Rain Bird",
            model=satellite.get("model", "Techlan Полив"),
            sw_version=satellite.get("version"),
        )

    @property
    def is_on(self) -> bool:
        return self._coordinator.data.get("connection", {}).get("isConnected", False) if self._coordinator.data else False

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
        self.async_on_remove(
            self._config_coordinator.async_add_listener(self.async_write_ha_state)
        )


class RainBirdAnyZoneRunningBinarySensor(BinarySensorEntity):
    """Binary sensor reporting whether any zone is currently running — real-time.

    Saves comparing every Station sensor individually or building a template
    helper — a single entity for automations like "pause music while
    irrigation is running" or "notify when irrigation starts".
    """

    def __init__(
        self,
        coordinator: RainBirdCoordinator,
        config_coordinator: RainBirdConfigCoordinator,
    ) -> None:
        self._coordinator = coordinator
        self._config_coordinator = config_coordinator
        satellite = config_coordinator.data.get("satellite", {}) if config_coordinator.data else {}
        self._satellite_id = coordinator.satellite_id
        self._satellite_name = satellite.get("name", "Techlan Полив")
        self._attr_unique_id = f"{self._satellite_id}_any_zone_running"
        self._attr_name = "Полив идёт"
        self._attr_device_class = BinarySensorDeviceClass.RUNNING
        self._attr_icon = "mdi:sprinkler-variant"

    @property
    def device_info(self) -> DeviceInfo:
        satellite = self._config_coordinator.data.get("satellite", {}) if self._config_coordinator.data else {}
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._satellite_id))},
            name=self._satellite_name,
            manufacturer="Rain Bird",
            model=satellite.get("model", "Techlan Полив"),
            sw_version=satellite.get("version"),
        )

    def _running_stations(self) -> list[dict]:
        if not self._coordinator.data:
            return []
        return [s for s in self._coordinator.data.get("stations", []) if s.get("isRunning")]

    @property
    def is_on(self) -> bool:
        return bool(self._running_stations())

    @property
    def extra_state_attributes(self) -> dict:
        running = self._running_stations()
        return {"running_zones": [s.get("name") for s in running]} if running else {}

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
        self.async_on_remove(
            self._config_coordinator.async_add_listener(self.async_write_ha_state)
        )


class RainBirdForecastBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor reporting whether forecast rain delay is enabled — config polling."""

    def __init__(self, coordinator: RainBirdConfigCoordinator) -> None:
        super().__init__(coordinator)
        self._satellite_id = coordinator.satellite_id
        satellite = coordinator.data.get("satellite", {}) if coordinator.data else {}
        self._satellite_name = satellite.get("name", "Techlan Полив")
        self._attr_unique_id = f"{self._satellite_id}_forecast_enabled"
        self._attr_name = "Задержка по прогнозу"
        self._attr_device_class = BinarySensorDeviceClass.RUNNING
        self._attr_icon = "mdi:weather-lightning-rainy"

    @property
    def device_info(self) -> DeviceInfo:
        satellite = self.coordinator.data.get("satellite", {}) if self.coordinator.data else {}
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._satellite_id))},
            name=self._satellite_name,
            manufacturer="Rain Bird",
            model=satellite.get("model", "Techlan Полив"),
            sw_version=satellite.get("version"),
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.get("forecast", {}).get("enabled", False) if self.coordinator.data else False

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        forecast = self.coordinator.data.get("forecast", {})
        if not forecast.get("enabled"):
            return {}
        return {
            "percent":    forecast.get("percent"),
            "rainfall":   forecast.get("inches"),
            "delay_days": forecast.get("delayDays"),
        }


class RainBirdLocalSensorBinarySensor(BinarySensorEntity):
    """State of the controller's local sensor (SEN) terminals — real-time.

    Reports the electrical state of the sensor terminals, not a particular
    sensor model. `state: 1` means the circuit is open, which is what a rain
    sensor does when it trips and what makes the controller suspend
    irrigation; `state: 0` means closed. Controllers shipped without a sensor
    carry a factory jumper across those terminals and therefore read dry.

    The terminals accept more than rain sensors (controllers advertise
    several localSensorTypes, freeze sensors among them), so the entity is
    named after the terminals rather than after rain.

    Source is AppSync: GetSensorListBySatelliteId's `onOffState` was observed
    to stay at 0 with the terminals both bridged and open, so the REST API
    cannot back this.
    """

    def __init__(
        self,
        coordinator: RainBirdCoordinator,
        config_coordinator: RainBirdConfigCoordinator,
    ) -> None:
        self._coordinator = coordinator
        self._config_coordinator = config_coordinator
        satellite = (config_coordinator.data or {}).get("satellite", {})
        self._satellite_id = coordinator.satellite_id
        self._satellite_name = satellite.get("name", "Techlan Полив")
        self._attr_unique_id = f"{self._satellite_id}_local_sensor"
        self._attr_name = "Датчик дождя"
        self._attr_device_class = BinarySensorDeviceClass.MOISTURE
        self._attr_icon = "mdi:water-alert"

    @property
    def device_info(self) -> DeviceInfo:
        satellite = (self._config_coordinator.data or {}).get("satellite", {})
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._satellite_id))},
            name=self._satellite_name,
            manufacturer="Rain Bird",
            model=satellite.get("model", "Techlan Полив"),
            sw_version=satellite.get("version"),
        )

    def _state_record(self) -> dict | None:
        if not self._coordinator.data:
            return None
        return self._coordinator.data.get("localSensor")

    @property
    def is_on(self) -> bool:
        record = self._state_record()
        return bool(record and record.get("state") == 1)

    @property
    def available(self) -> bool:
        # Without a reading, report unavailable rather than claiming dry.
        return (
            self._coordinator.last_update_success
            and self._state_record() is not None
        )

    @property
    def extra_state_attributes(self) -> dict:
        record = self._state_record() or {}
        attrs: dict = {"raw_state": record.get("state")}
        if record.get("timestamp") is not None:
            attrs["last_reported"] = record["timestamp"]

        # What IQ4 believes is wired to the terminals, for context only. Both
        # fields have been observed to disagree with the hardware actually
        # connected, so they never drive the state.
        sensors = (self._config_coordinator.data or {}).get("sensors", [])
        if sensors:
            attrs["configured_model"] = sensors[0].get("model")
            attrs["configured_type"] = sensors[0].get("typeName")
        return attrs

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
        self.async_on_remove(
            self._config_coordinator.async_add_listener(self.async_write_ha_state)
        )


class RainBirdRainSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for a sensor declared in IQ4 — config polling.

    NOTE: only instantiated for sensors reporting `type != -1`, and no such
    controller has been observed. The `triggered` and `active` fields read
    below do not appear in any captured payload of
    GetSensorListBySatelliteId, so this entity would sit permanently off if
    it were ever created. Left as-is rather than guessed at: fixing it needs
    a dump from a controller that actually declares a sensor. For the SEN
    terminals themselves, see RainBirdLocalSensorBinarySensor above.
    """

    def __init__(self, coordinator: RainBirdConfigCoordinator, sensor: dict) -> None:
        super().__init__(coordinator)
        self._sensor_id = sensor["id"]
        self._satellite_id = coordinator.satellite_id
        satellite = coordinator.data.get("satellite", {}) if coordinator.data else {}
        self._satellite_name = satellite.get("name", "Techlan Полив")
        self._attr_unique_id = f"{self._satellite_id}_sensor_{self._sensor_id}"
        self._attr_name = f"Датчик: {sensor.get('name', 'дождь')}"
        self._attr_device_class = BinarySensorDeviceClass.MOISTURE
        self._attr_icon = "mdi:weather-rainy"

    @property
    def device_info(self) -> DeviceInfo:
        satellite = self.coordinator.data.get("satellite", {}) if self.coordinator.data else {}
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._satellite_id))},
            name=self._satellite_name,
            manufacturer="Rain Bird",
            model=satellite.get("model", "Techlan Полив"),
            sw_version=satellite.get("version"),
        )

    def _get_sensor(self) -> dict:
        if not self.coordinator.data:
            return {}
        for s in self.coordinator.data.get("sensors", []):
            if s["id"] == self._sensor_id:
                return s
        return {}

    @property
    def is_on(self) -> bool:
        return bool(self._get_sensor().get("triggered", False))

    @property
    def extra_state_attributes(self) -> dict:
        sensor = self._get_sensor()
        return {
            "model":  sensor.get("model"),
            "type":   sensor.get("typeName"),
            "active": sensor.get("active"),
        }


# ── Камера-детектор осадков (добавлено Techlan) ───────────────────────────────

def _rain_detector(hass, entry):
    return hass.data.get(f"{DOMAIN}_store", {}).get(f"detector:{entry.entry_id}")


class RainTechlanCameraRain(BinarySensorEntity):
    """Дождь по камере (влажное покрытие в зонах наблюдения)."""

    _attr_should_poll = True
    _attr_has_entity_name = True
    _attr_name = "Дождь (камера)"
    _attr_device_class = "moisture"

    def __init__(self, hass, entry) -> None:
        self.hass = hass
        self.entry = entry
        from .const import CONF_SATELLITE_ID
        self._satellite_id = entry.data.get(CONF_SATELLITE_ID, "")
        self._attr_unique_id = f"{entry.entry_id}_cam_rain"

    @property
    def device_info(self):
        return DeviceInfo(identifiers={(DOMAIN, str(getattr(self, "_satellite_id", "")))},
                          manufacturer="Techlan", model="Камера-детектор осадков")

    @property
    def is_on(self) -> bool:
        det = _rain_detector(self.hass, self.entry)
        return bool(det and det.last.get("wet"))

    @property
    def extra_state_attributes(self) -> dict:
        det = _rain_detector(self.hass, self.entry)
        if not det:
            return {}
        s = det.last.get("summary") or {}
        return {"оценка": s.get("score"), "вердикт": s.get("verdict"), "зона": s.get("zone")}
