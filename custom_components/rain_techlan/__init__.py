"""Techlan Полив integration for Home Assistant."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.start import async_at_started
from homeassistant.loader import async_get_integration

from .api import RainBirdAPI
from .auth import RainBirdAuth
from .const import (
    CONF_AUTH_CHANNEL,
    CONF_COMPANY_ID,
    CONF_PASSWORD,
    CONF_SATELLITE_ID,
    CONF_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL_CONFIG,
    CONF_SCAN_INTERVAL_PROGRAM,
    CONF_USERNAME,
    DEFAULT_AUTH_CHANNEL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_CONFIG,
    DEFAULT_SCAN_INTERVAL_PROGRAM,
    DOMAIN,
)
from .coordinator import RainBirdCoordinator, RainBirdConfigCoordinator, RainBirdProgramCoordinator

import asyncio
from datetime import timedelta

from homeassistant.helpers.event import async_track_time_interval

from .camera import Detector
from .const import (
    CONF_CAM_THRESHOLD,
    CONF_CAMERAS,
    CONF_SCAN_CAMERA,
    CONF_TRUTH_ENTITY,
    CONF_ZONES,
    DEFAULT_SCAN_CAMERA,
)
from .interlocks import evaluate as evaluate_interlocks
from .panel import async_register_panel, async_unregister_panel
from .settings import get_settings
from .views import async_register_views

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "calendar", "button", "switch", "number", "image"]
FRONTEND_URL = f"/{DOMAIN}/rain_techlan_card.js"
FRONTEND_PATH = Path(__file__).parent / "frontend" / "rain_techlan_card.js"
_FRONTEND_REGISTERED = False
_CARD_RESOURCE_VERSIONED = False

SERVICES = [
    "scan",
    "learn_dry",
    "simulate_rain",
    "start_zone",
    "start_program",
    "stop_zone",
    "stop_all_zones",
    "set_rain_delay",
    "enable_forecast_rain_delay",
    "disable_forecast_rain_delay",
    "set_weather_adjust_automatic",
    "set_weather_adjust_manual",
]


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Register the bundled Lovelace card frontend file."""
    global _FRONTEND_REGISTERED
    if _FRONTEND_REGISTERED:
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL, str(FRONTEND_PATH), cache_headers=False)]
    )
    _FRONTEND_REGISTERED = True


async def _async_version_card_resource(hass: HomeAssistant) -> None:
    """Keep the card's Lovelace resource URL on ?v=<integration version>.

    The card is served from a fixed path, so after an update browsers and the
    Companion app happily keep running the module they already cached. Putting
    the version in the registered resource URL changes that URL on every
    release, which is what forces the refetch — the same trick HACS uses with
    its ?hacstag= parameter.

    Best effort on purpose: Lovelace's resource storage is an internal API and
    YAML mode has no writable collection, so every failure path simply leaves
    the URL untouched, exactly as it behaved before 1.4.2.
    """
    global _CARD_RESOURCE_VERSIONED
    if _CARD_RESOURCE_VERSIONED:
        return
    try:
        version = (await async_get_integration(hass, DOMAIN)).version
        if version is None:
            return
        target = f"{FRONTEND_URL}?v={version}"

        # Read hass.data instead of importing lovelace internals: that import
        # path has moved between HA releases and an ImportError at module
        # level would take the whole integration down.
        lovelace = hass.data.get("lovelace")
        collection = getattr(lovelace, "resources", None)
        if collection is None and isinstance(lovelace, dict):
            collection = lovelace.get("resources")
        if collection is None or not hasattr(collection, "async_update_item"):
            _LOGGER.debug(
                "Lovelace resources are not writable (YAML mode?); card URL left as %s",
                FRONTEND_URL,
            )
            return

        await collection.async_get_info()  # loads the collection if needed
        for item in collection.async_items() or []:
            url = item.get("url", "")
            if url.split("?", 1)[0] != FRONTEND_URL or url == target:
                continue
            await collection.async_update_item(item["id"], {"url": target})
            _LOGGER.info("Card resource URL updated to %s", target)
        _CARD_RESOURCE_VERSIONED = True
    except Exception:  # noqa: BLE001 - a cosmetic URL must never block setup
        _LOGGER.debug("Could not version the card resource URL", exc_info=True)


@callback
def _async_schedule_card_resource_version(hass: HomeAssistant) -> None:
    """Version the card resource once HA is up.

    Lovelace may not have built its data yet while config entries are being
    set up, so the update waits for the started event unless HA is already
    running, which is the case when the entry is reloaded. async_at_started
    covers both cases and, because the callback is a coroutine function, runs
    it on the event loop instead of in a worker thread.
    """
    async def _version_resource(_hass: HomeAssistant) -> None:
        await _async_version_card_resource(hass)

    async_at_started(hass, _version_resource)


# ── Entity resolution via unique_id (stable, no name matching) ────────────────

def _resolve_unique_id(hass: HomeAssistant, entity_id: str, kind: str) -> tuple[str, int]:
    """Resolve an entity_id to (satellite_id, object_id) via the entity registry.

    kind is "station" or "program"; the unique_id format is
    "{satellite_id}_station_{station_id}" / "{satellite_id}_program_{program_id}".
    """
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    if not entry:
        raise ServiceValidationError(
            f"Entity {entity_id} not found in the entity registry"
        )

    marker = f"_{kind}_"
    unique_id = entry.unique_id or ""
    if entry.platform != DOMAIN or marker not in unique_id:
        raise ServiceValidationError(
            f"Entity {entity_id} is not a Techlan Полив {kind} sensor. "
            f"Please select a {kind.capitalize()} sensor."
        )

    satellite_id_str, _, object_id_str = unique_id.rpartition(marker)
    try:
        object_id = int(object_id_str)
    except ValueError:
        raise ServiceValidationError(
            f"Entity {entity_id} has an unexpected unique_id format: {unique_id}"
        )
    return satellite_id_str, object_id


def _coordinators_for_satellite(hass: HomeAssistant, satellite_id_str: str) -> dict:
    """Find the coordinators dict for a given satellite id string."""
    for coordinators in hass.data.get(DOMAIN, {}).values():
        realtime = coordinators.get("realtime")
        if realtime and str(realtime.satellite_id) == satellite_id_str:
            return coordinators
    raise ServiceValidationError(
        f"No active Techlan Полив controller found for satellite {satellite_id_str}. "
        f"Is the integration loaded?"
    )


def _resolve_station(
    hass: HomeAssistant, entity_id: str
) -> tuple[int, RainBirdAPI, RainBirdCoordinator]:
    """Resolve a station sensor entity_id to (station_id, api, realtime_coordinator)."""
    satellite_id_str, station_id = _resolve_unique_id(hass, entity_id, "station")
    coordinators = _coordinators_for_satellite(hass, satellite_id_str)
    return station_id, coordinators["api"], coordinators["realtime"]


def _resolve_program(
    hass: HomeAssistant, entity_id: str
) -> tuple[int, RainBirdAPI, RainBirdProgramCoordinator]:
    """Resolve a program sensor entity_id to (program_id, api, program_coordinator)."""
    satellite_id_str, program_id = _resolve_unique_id(hass, entity_id, "program")
    coordinators = _coordinators_for_satellite(hass, satellite_id_str)
    return program_id, coordinators["api"], coordinators["program"]


def _resolve_controller(hass: HomeAssistant, entity_id: str | None) -> dict:
    """Return the coordinators dict for a controller-level service call.

    If an entity is provided, any Techlan Полив entity of that controller works:
    the satellite id is the prefix of every unique_id this integration creates.
    If omitted, fall back to the single configured entry (backwards compatible),
    or raise a clear error when multiple controllers exist.
    """
    if entity_id:
        ent_reg = er.async_get(hass)
        entry = ent_reg.async_get(entity_id)
        if not entry or entry.platform != DOMAIN or not entry.unique_id:
            raise ServiceValidationError(
                f"Entity {entity_id} is not a Techlan Полив entity."
            )
        satellite_id_str = entry.unique_id.split("_")[0]
        return _coordinators_for_satellite(hass, satellite_id_str)

    entries = list(hass.data.get(DOMAIN, {}).values())
    if len(entries) == 1:
        return entries[0]
    raise ServiceValidationError(
        "Multiple Techlan Полив controllers are configured. "
        "Please select a controller entity in the service call."
    )


# ── Domain-level service handlers (registered once) ───────────────────────────

# ── Камера-детектор осадков: реакции и защита ─────────────────────────────────

def _camera_stores(hass: HomeAssistant) -> tuple[list, list]:
    """(settings, detector) всех загруженных записей."""
    settings, detectors = [], []
    for key, val in hass.data.get(f"{DOMAIN}_store", {}).items():
        if key.startswith("settings:"):
            settings.append(val)
        elif key.startswith("detector:"):
            detectors.append(val)
    return settings, detectors


def _guard_interlocks(hass: HomeAssistant, action: str) -> None:
    """Проверить запреты перед командой полива; при запрете — отклонить."""
    rules: list = []
    for key, val in hass.data.get(f"{DOMAIN}_store", {}).items():
        if key.startswith("settings:"):
            rules.extend(val.interlocks or [])
    if not rules:
        return
    # превентивно: если ждут дождь (прогноз) — не начинаем полив
    for key, val in hass.data.get(f"{DOMAIN}_store", {}).items():
        if not key.startswith("settings:"):
            continue
        for ent in (getattr(val, "forecast_entities", None) or []):
            st = hass.states.get(ent)
            if st is not None and st.state == "on":
                hass.bus.async_fire("rain_techlan_action", {"event": "forecast_block", "entity": ent})
                raise ServiceValidationError(f"Полив не начат: ожидается дождь ({ent})")
    res = evaluate_interlocks(hass, rules, action)
    if res.get("blocked"):
        hass.bus.async_fire("rain_techlan_interlock",
                            {"action": action, "rules": res.get("block_text")})
        raise ServiceValidationError(f"Полив запрещён: {res.get('block_text')}")


async def _async_run_reactions(hass: HomeAssistant, settings, detector, prev_wet: bool) -> None:
    """Реакции на смену состояния «дождь ↔ сухо»."""
    now_wet = bool(detector.last.get("wet"))
    if now_wet == prev_wet:
        return
    cfg = (settings.reactions or {}).get("rain_start" if now_wet else "rain_end") or {}
    if not cfg.get("enabled", True):
        return
    summary = detector.last.get("summary") or {}
    hass.bus.async_fire("rain_techlan_action", {
        "event": "rain_start" if now_wet else "rain_end",
        "sources": detector.last.get("sources") or {},
        "score": (detector.last.get("summary") or {}).get("score"),
    })
    if now_wet and cfg.get("stop_all"):
        try:
            await hass.services.async_call(DOMAIN, "stop_all_zones", {}, blocking=False)
            detector.add_journal("action", "Полив остановлен (дождь)")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("rain_techlan: стоп по дождю не удался: %s", err)
    days = int(cfg.get("delay_days") or 0)
    # пауза на дни — только при согласии источников (или если камера недоступна)
    summary = detector.last.get("summary") or {}
    sources = (detector.last.get("sources") or {})
    camera_ok = detector.last.get("camera_ok", True)
    agree = (sources.get("truth") and sources.get("camera")) or (sources.get("truth") and not camera_ok)
    if now_wet and days > 0 and agree:
        try:
            await hass.services.async_call(DOMAIN, "set_rain_delay", {"days": days}, blocking=False)
            detector.add_journal("action", f"Задержка полива: {days} дн. (дождь)")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("rain_techlan: задержка дождя не выставлена: %s", err)
    if (not now_wet) and cfg.get("clear_delay"):
        try:
            await hass.services.async_call(DOMAIN, "set_rain_delay", {"days": 0}, blocking=False)
            detector.add_journal("action", "Задержка полива снята (дождь закончился)")
        except Exception:  # noqa: BLE001
            pass
    if cfg.get("notify"):
        try:
            await hass.services.async_call(
                "persistent_notification", "create",
                {"title": "Полив: дождь" if now_wet else "Полив: дождь закончился",
                 "message": (f"Камера: {summary.get('verdict')} (оценка {summary.get('score')}, "
                             f"зона «{summary.get('zone')}»). "
                             + ("Полив остановлен." if now_wet else "Полив снова возможен.")),
                 "notification_id": "rain_techlan_rain"},
                blocking=False)
        except Exception:  # noqa: BLE001
            pass


async def _handle_camera_scan(call: ServiceCall) -> None:
    """Сервис: внеплановый проход камер."""
    hass = call.hass
    for detector in _camera_stores(hass)[1]:
        await detector.async_scan()


async def _handle_simulate_rain(call: ServiceCall) -> None:
    """Тест-режим: «wet»/«dry»/«auto» — смоделировать дождь для проверки цепочки."""
    hass = call.hass
    val = {"wet": True, "dry": False}.get(str(call.data.get("state") or "auto").lower())
    for det in _camera_stores(hass)[1]:
        det._sim_wet = val
        det.add_journal("test", f"Тест-режим: {call.data.get('state')}")


async def _handle_learn_dry(call: ServiceCall) -> None:
    """Сервис: пометить замеры «сухо» (для эталона)."""
    hass = call.hass
    hours = float(call.data.get("hours") or 24)
    for detector in _camera_stores(hass)[1]:
        await detector.async_mark_dry(hours)


async def _handle_start_zone(call: ServiceCall) -> None:
    hass = call.hass
    _guard_interlocks(hass, "start_zone")
    station_id, api, coordinator = _resolve_station(hass, call.data["station_entity"])
    duration = call.data["duration"]
    duration_seconds = duration * 60
    await hass.async_add_executor_job(api.start_station, station_id, duration_seconds)
    # Only reached if the line above didn't raise — i.e. the backend
    # actually accepted the command. See RainBirdCoordinator.set_optimistic_running
    # for why this exists: neither the live-status endpoint nor Rain Bird's
    # push channel reflect manually-started zones.
    coordinator.set_optimistic_running(station_id, duration_seconds)
    await coordinator.async_request_refresh()


async def _handle_start_program(call: ServiceCall) -> None:
    hass = call.hass
    _guard_interlocks(hass, "start_program")
    program_id, api, program_coordinator = _resolve_program(hass, call.data["program_entity"])
    await hass.async_add_executor_job(api.start_program, program_id)
    # No optimistic state needed: program-triggered runs are already
    # correctly reflected by GetRunStationStatusForSatellite.
    await program_coordinator.async_request_refresh()


async def _handle_stop_zone(call: ServiceCall) -> None:
    hass = call.hass
    station_id, api, coordinator = _resolve_station(hass, call.data["station_entity"])
    await hass.async_add_executor_job(api.stop_station, station_id)
    coordinator.mark_stopped(station_id)
    await coordinator.async_request_refresh()


async def _handle_stop_all_zones(call: ServiceCall) -> None:
    hass = call.hass
    coordinators = _resolve_controller(hass, call.data.get("controller_entity"))
    api = coordinators["api"]
    realtime = coordinators["realtime"]
    # StopAllIrrigation needs no station ids and stops queued program
    # stations too, so it is sent even when nothing looks running. The ids
    # only feed the AdvanceStations fallback and the manual-stop marks below.
    # Reuse the realtime coordinator's already-cached data instead of an
    # extra live status call — zero additional API cost in the common case.
    # None (no data yet) makes the fallback target every station.
    running_ids = None
    if realtime.data:
        running_ids = [
            s["id"] for s in realtime.data.get("stations", []) if s.get("isRunning")
        ]
    await hass.async_add_executor_job(api.stop_all_stations, realtime.satellite_id, running_ids)
    ids_to_mark = running_ids
    if ids_to_mark is None and realtime.data:
        ids_to_mark = [s["id"] for s in realtime.data.get("stations", [])]
    for sid in (ids_to_mark or []):
        realtime.mark_stopped(sid)
    await realtime.async_request_refresh()


async def _handle_set_rain_delay(call: ServiceCall) -> None:
    hass = call.hass
    coordinators = _resolve_controller(hass, call.data.get("controller_entity"))
    api = coordinators["api"]
    config_coordinator = coordinators["config"]
    await hass.async_add_executor_job(
        api.set_rain_delay, config_coordinator.satellite_id, call.data["days"]
    )
    await config_coordinator.async_request_refresh()


async def _handle_enable_forecast(call: ServiceCall) -> None:
    hass = call.hass
    coordinators = _resolve_controller(hass, call.data.get("controller_entity"))
    api = coordinators["api"]
    config_coordinator = coordinators["config"]
    await hass.async_add_executor_job(
        api.set_forecast, config_coordinator.satellite_id, True,
        int(call.data["percent"]), float(call.data["rainfall"]), int(call.data["delay_days"]),
    )
    await config_coordinator.async_request_refresh()


async def _handle_disable_forecast(call: ServiceCall) -> None:
    hass = call.hass
    coordinators = _resolve_controller(hass, call.data.get("controller_entity"))
    api = coordinators["api"]
    config_coordinator = coordinators["config"]
    await hass.async_add_executor_job(
        api.set_forecast, config_coordinator.satellite_id, False
    )
    await config_coordinator.async_request_refresh()


async def _handle_weather_adjust_automatic(call: ServiceCall) -> None:
    hass = call.hass
    program_id, api, program_coordinator = _resolve_program(hass, call.data["program_entity"])
    await hass.async_add_executor_job(api.set_weather_adjust_method, program_id, 7)
    await program_coordinator.async_request_refresh()


async def _handle_weather_adjust_manual(call: ServiceCall) -> None:
    hass = call.hass
    program_id, api, program_coordinator = _resolve_program(hass, call.data["program_entity"])
    seasonal_adjust = call.data.get("seasonal_adjust", 100)
    await hass.async_add_executor_job(api.set_weather_adjust_method, program_id, 6)
    await hass.async_add_executor_job(api.set_seasonal_adjust, program_id, seasonal_adjust)
    await program_coordinator.async_request_refresh()


_SERVICE_HANDLERS = {
    "start_zone": _handle_start_zone,
    "start_program": _handle_start_program,
    "stop_zone": _handle_stop_zone,
    "stop_all_zones": _handle_stop_all_zones,
    "set_rain_delay": _handle_set_rain_delay,
    "enable_forecast_rain_delay": _handle_enable_forecast,
    "disable_forecast_rain_delay": _handle_disable_forecast,
    "set_weather_adjust_automatic": _handle_weather_adjust_automatic,
    "set_weather_adjust_manual": _handle_weather_adjust_manual,
    "scan": _handle_camera_scan,
    "learn_dry": _handle_learn_dry,
    "simulate_rain": _handle_simulate_rain,
}


def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain services once, regardless of how many entries exist."""
    for service, handler in _SERVICE_HANDLERS.items():
        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(DOMAIN, service, handler)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Techlan Полив from a config entry."""
    username     = entry.data[CONF_USERNAME]
    password     = entry.data[CONF_PASSWORD]
    satellite_id = entry.data[CONF_SATELLITE_ID]
    company_id   = entry.data[CONF_COMPANY_ID]

    scan_realtime = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    scan_config   = entry.options.get(CONF_SCAN_INTERVAL_CONFIG, DEFAULT_SCAN_INTERVAL_CONFIG)
    scan_program  = entry.options.get(CONF_SCAN_INTERVAL_PROGRAM, DEFAULT_SCAN_INTERVAL_PROGRAM)

    # Auth channel: chosen at install (entry.data), optionally overridden
    # later via the options flow (entry.options takes precedence).
    auth_channel = entry.options.get(
        CONF_AUTH_CHANNEL,
        entry.data.get(CONF_AUTH_CHANNEL, DEFAULT_AUTH_CHANNEL),
    )

    auth = RainBirdAuth(hass, username, password, channel=auth_channel)
    api  = RainBirdAPI(auth)

    coordinator         = RainBirdCoordinator(hass, api, satellite_id, company_id, scan_realtime)
    config_coordinator  = RainBirdConfigCoordinator(hass, api, satellite_id, scan_config)
    program_coordinator = RainBirdProgramCoordinator(hass, api, satellite_id, scan_program)

    try:
        await coordinator.async_config_entry_first_refresh()
        await config_coordinator.async_config_entry_first_refresh()
        await program_coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Unable to connect to Rain Bird: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "realtime": coordinator,
        "config":   config_coordinator,
        "program":  program_coordinator,
        "api":      api,
    }

    # ── камера-детектор осадков (зрение) ─────────────────────────────────────
    settings = get_settings(hass, entry)
    detector = Detector(hass, settings.cameras, settings.zones,
                        settings.truth_entity, settings.threshold)
    await detector.async_init()
    store = hass.data.setdefault(f"{DOMAIN}_store", {})
    store[f"settings:{entry.entry_id}"] = settings
    store[f"detector:{entry.entry_id}"] = detector
    async_register_views(hass)
    await async_register_panel(hass)

    async def _camera_tick(_now=None) -> None:
        if not detector.cameras:
            return
        prev = bool(detector.last.get("wet"))
        try:
            await detector.async_scan()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("rain_techlan: проход камер не удался: %s", err)
            return
        await _async_run_reactions(hass, settings, detector, prev)

    unsub = async_track_time_interval(
        hass, _camera_tick, timedelta(seconds=DEFAULT_SCAN_CAMERA)
    )
    entry.async_on_unload(unsub)
    if detector.cameras:
        hass.async_create_task(_camera_tick())

    await _async_register_frontend(hass)
    _async_schedule_card_resource_version(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    _async_register_services(hass)

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Обновление настроек: «камерные» применяем на месте, остальное — перезагрузкой."""
    store = hass.data.get(f"{DOMAIN}_store", {})
    st = store.get(f"settings:{entry.entry_id}")
    det = store.get(f"detector:{entry.entry_id}")
    # ключи, которые применяются без перезагрузки (детектор, зоны, порог, запреты, реакции, времена)
    live_keys = {CONF_CAMERAS, CONF_ZONES, CONF_CAM_THRESHOLD, CONF_TRUTH_ENTITY,
                 CONF_INTERLOCKS, CONF_REACTIONS, "rain_delay_days", "zone_runtimes",
                 CONF_SCAN_CAMERA}
    try:
        if st is not None:
            st.reload()
        if st is not None and det is not None:
            det.cameras, det.zones = st.cameras, st.zones
            det.truth_entity, det.threshold = st.truth_entity, st.threshold
        if st is not None and not [k for k in (entry.options or {}) if k not in live_keys]:
            return          # всё изменённое применяется на лету — перезагрузка не нужна
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("rain_techlan: живое применение настроек не удалось: %s", err)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if entry_data:
        # Drop the realtime coordinator's pending confirmation refresh, so a
        # reload does not leave a timer firing against a dead coordinator.
        entry_data["realtime"].async_cancel_probe()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(f"{DOMAIN}_store", {}).pop(f"settings:{entry.entry_id}", None)
        hass.data.get(f"{DOMAIN}_store", {}).pop(f"detector:{entry.entry_id}", None)
        if not any(k.startswith("detector:") for k in hass.data.get(f"{DOMAIN}_store", {})):
            await async_unregister_panel(hass)
        coordinators = hass.data[DOMAIN].pop(entry.entry_id)
        api = coordinators.get("api")
        if api:
            await hass.async_add_executor_job(api.close)
        # Only remove domain services when the last loaded entry goes away
        if not hass.data[DOMAIN]:
            for service in SERVICES:
                hass.services.async_remove(DOMAIN, service)
    return unload_ok
