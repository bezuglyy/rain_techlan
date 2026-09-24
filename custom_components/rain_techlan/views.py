"""HTTP-API панели «Полив и осадки»: кадр с зонами, состояние, настройки, журнал, запреты."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CAM_THRESHOLD,
    CONF_CAMERAS,
    CONF_INTERLOCKS,
    CONF_REACTIONS,
    CONF_TRUTH_ENTITY,
    CONF_ZONES,
    DOMAIN,
    URL_EVENTS,
    URL_FRAME,
    URL_INTERLOCKS,
    URL_LEARN,
    URL_SETTINGS,
    URL_STATE,
    URL_TEST,
)
from .interlocks import active_for_actions, normalize_interlocks
from .settings import get_settings

_LOGGER = logging.getLogger(__name__)


def _require_admin(request: web.Request) -> None:
    user = request.get("hass_user")
    if user is not None and not user.is_admin:
        raise web.HTTPForbidden()


def _detector(request: web.Request):
    hass: HomeAssistant = request.app["hass"]
    for key, val in hass.data.get(f"{DOMAIN}_store", {}).items():
        if key.startswith("detector:"):
            return val
    raise web.HTTPServiceUnavailable(text="детектор не запущен")


def _settings(request: web.Request):
    hass: HomeAssistant = request.app["hass"]
    for key, val in hass.data.get(f"{DOMAIN}_store", {}).items():
        if key.startswith("settings:"):
            return val
    raise web.HTTPServiceUnavailable(text="настройки не найдены")


class RainFrameView(HomeAssistantView):
    url = URL_FRAME
    name = "api:rain_techlan:frame"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        det = _detector(request)
        camera = request.query.get("camera") or (
            det.cameras[0]["id"] if det.cameras else ""
        )
        raw = request.query.get("raw") == "1"
        data = None
        if camera:
            path = det.dir / "last" / f"{camera}.jpg"
            if raw and path.exists():
                data = path.read_bytes()
            else:
                data = await det.async_annotated_frame(camera)
        if not data:
            return web.HTTPNotFound(text="нет кадра — сделайте проход")
        return web.Response(
            body=data, content_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )


class RainStateView(HomeAssistantView):
    url = URL_STATE
    name = "api:rain_techlan:state"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        det = _detector(request)
        st = _settings(request)
        hass: HomeAssistant = request.app["hass"]
        data = {
            "detector": det.state(),
            "settings": st.as_dict(),
            "interlocks_active": active_for_actions(hass, st.interlocks),
        }
        return self.json(data)


class RainSettingsView(HomeAssistantView):
    url = URL_SETTINGS
    name = "api:rain_techlan:settings"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        return self.json(_settings(request).as_dict())

    async def post(self, request: web.Request) -> web.Response:
        _require_admin(request)
        st = _settings(request)
        body = await request.json()
        allowed = (
            CONF_CAMERAS,
            CONF_ZONES,
            CONF_CAM_THRESHOLD,
            CONF_TRUTH_ENTITY,
            CONF_REACTIONS,
            CONF_INTERLOCKS,
        )
        patch = {k: v for k, v in (body or {}).items() if k in allowed}
        if not patch:
            return self.json({"ok": False, "error": "нет допустимых полей"})
        await st.async_save(patch)
        det = _detector(request)
        det.cameras, det.zones = st.cameras, st.zones
        det.truth_entity, det.threshold = st.truth_entity, st.threshold
        det.add_journal("settings", "Настройки обновлены: " + ", ".join(sorted(patch)))
        return self.json({"ok": True, "settings": st.as_dict()})


class RainScanView(HomeAssistantView):
    url = URL_TEST
    name = "api:rain_techlan:scan"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        _require_admin(request)
        det = _detector(request)
        result = await det.async_scan()
        return self.json({"ok": True, "detector": result})


class RainLearnView(HomeAssistantView):
    url = URL_LEARN
    name = "api:rain_techlan:learn"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        _require_admin(request)
        det = _detector(request)
        body = {}
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            pass
        hours = float((body or {}).get("hours") or 24)
        changed = await det.async_mark_dry(hours)
        return self.json({"ok": True, "marked": changed, "groups": len(det.reference)})


class RainEventsView(HomeAssistantView):
    url = URL_EVENTS
    name = "api:rain_techlan:events"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        det = _detector(request)
        return self.json({"journal": det.journal[-100:]})


class RainInterlocksView(HomeAssistantView):
    url = URL_INTERLOCKS
    name = "api:rain_techlan:interlocks"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        st = _settings(request)
        return self.json(
            {"rules": st.interlocks, "active": active_for_actions(hass, st.interlocks)}
        )

    async def post(self, request: web.Request) -> web.Response:
        _require_admin(request)
        st = _settings(request)
        body = await request.json()
        rules = normalize_interlocks((body or {}).get("rules"))
        await st.async_save({CONF_INTERLOCKS: rules})
        hass: HomeAssistant = request.app["hass"]
        return self.json(
            {
                "ok": True,
                "rules": st.interlocks,
                "active": active_for_actions(hass, st.interlocks),
            }
        )


def async_register_views(hass: HomeAssistant) -> None:
    for view in (
        RainFrameView(),
        RainStateView(),
        RainSettingsView(),
        RainScanView(),
        RainLearnView(),
        RainEventsView(),
        RainInterlocksView(),
    ):
        hass.http.register_view(view)
