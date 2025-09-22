from __future__ import annotations

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import (
    DOMAIN,
    CONF_BASE_URL,
    CONF_USERNAME,
    CONF_PASSWORD,
    STORE_DIR,
    STORE_LAST_HTML,
    STORE_LAST_JSON,
)
from .api import HomeInfoPointClient, InvalidAuth, CannotConnect
from .coordinator import HomeInfoPointCoordinator, _write_text


async def async_setup(hass: HomeAssistant, config) -> bool:
    """Globale Initialisierung (Services registrieren)."""

    if not hass.services.has_service(DOMAIN, "refresh"):
        async def _svc_refresh(call):
            for data in hass.data.get(DOMAIN, {}).values():
                coord = data.get("coordinator")
                if coord:
                    await coord.async_request_refresh()
        hass.services.async_register(DOMAIN, "refresh", _svc_refresh)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    base_url = entry.data[CONF_BASE_URL]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    client = HomeInfoPointClient(hass, base_url)

    try:
        await client.async_login(username, password)
        html = await client.async_login_and_fetch_html()
    except InvalidAuth as err:
        raise ConfigEntryAuthFailed from err
    except CannotConnect:
        raise
    except Exception as err:
        raise CannotConnect(str(err)) from err

    # entry-eigene Ablage /config/homeinfopoint/<entry_id>/last.html
    entry_folder = Path(hass.config.path(STORE_DIR, entry.entry_id))
    await hass.async_add_executor_job(_write_text, entry_folder / STORE_LAST_HTML, html)

    coordinator = HomeInfoPointCoordinator(hass, client)
    coordinator._entry_folder = entry_folder  # Pfad bekannt geben
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "calendar"])

    # Bei Optionsänderungen neu laden (z. B. Fachnamen / Kalender-Mapping)
    entry.async_on_unload(entry.add_update_listener(_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    stored = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    client: HomeInfoPointClient | None = stored.get("client") if stored else None

    unloaded = await hass.config_entries.async_unload_platforms(entry, ["sensor", "calendar"])
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)

    if client:
        await client.async_close()

    return unloaded


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
