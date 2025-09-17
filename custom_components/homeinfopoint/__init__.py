from __future__ import annotations

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
)
from .api import HomeInfoPointClient, InvalidAuth, CannotConnect
from .coordinator import HomeInfoPointCoordinator


async def async_setup(hass: HomeAssistant, config) -> bool:
    """YAML-Setup wird nicht genutzt."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """ConfigEntry laden: einloggen, HTML holen, speichern, Coordinator & Plattformen starten."""
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
    except Exception as err:  # noqa: BLE001
        raise CannotConnect(str(err)) from err

    # HTML zwischenspeichern
    await _async_write_text(hass, hass.config.path(STORE_DIR, STORE_LAST_HTML), html)

    # Coordinator starten (erste Aktualisierung sofort)
    coordinator = HomeInfoPointCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    # Plattformen laden
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "calendar"])

    # Auf Options-Änderungen reagieren (z. B. Fach-Namen → Sensoren neu benennen)
    entry.async_on_unload(entry.add_update_listener(_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Integration entladen."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, ["sensor", "calendar"])
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Bei Options-Änderungen (subject_map) Eintrag neu laden."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_write_text(hass: HomeAssistant, path: str, text: str) -> None:
    """Datei sicher im Executor schreiben."""
    def _write() -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    await hass.async_add_executor_job(_write)
