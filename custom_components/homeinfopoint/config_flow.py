from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    CONF_BASE_URL,
    CONF_USERNAME,
    CONF_PASSWORD,
    DEFAULT_BASE_URL,
    STORE_DIR,
    STORE_LAST_JSON,
)
from .api import HomeInfoPointClient, InvalidAuth, CannotConnect
from .parser import parse as parse_html  # für Fächer-Erkennung

# optionale Default-Anzeigenamen
SUBJECT_DEFAULTS = {
    "de": "Deutsch",
    "ma": "Mathematik",
    "en": "Englisch",
    "bio": "Biologie",
    "ch": "Chemie",
    "ph": "Physik",
    "ge": "Geschichte",
    "ek": "Erdkunde",
    "sp": "Sport",
    "mu": "Musik",
    "ku": "Kunst",
    "inf": "Informatik",
    "fr": "Französisch",
    "la": "Latein",
}

def _normalize_base_url(url: str) -> str:
    url = (url or "").strip()
    if not url.endswith("/"):
        url += "/"
    return url


class HomeInfoPointConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config-Flow für Home.InfoPoint mit Fächer-Mapping im Installationsprozess."""

    VERSION = 1

    # Zwischenspeicher zwischen Schritten
    _staged_data: dict[str, Any] | None = None
    _detected_subjects: list[str] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input[CONF_BASE_URL] = _normalize_base_url(user_input[CONF_BASE_URL])

            # Eindeutigkeit: base_url + username
            unique_id = f"{user_input[CONF_BASE_URL]}::{user_input[CONF_USERNAME]}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            # Login + erste Seite abrufen (inkl. unserem robusten Flow)
            try:
                client = HomeInfoPointClient(self.hass, user_input[CONF_BASE_URL])
                await client.async_login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
                html = await client.async_login_and_fetch_html()
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"

            if not errors:
                # Fächer aus HTML parsen
                try:
                    parsed = parse_html(html)
                    grades = parsed.get("grades") or {}
                    self._detected_subjects = sorted(grades.keys())
                except Exception:
                    # Parser nicht kritisch für Installation
                    self._detected_subjects = []

                # Daten für create_entry vormerken
                self._staged_data = {
                    CONF_BASE_URL: user_input[CONF_BASE_URL],
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                }

                # Wenn Fächer erkannt → Schritt "subjects", sonst direkt anlegen
                if self._detected_subjects:
                    return await self.async_step_subjects()

                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data=self._staged_data,
                    options={},  # später per Optionen bearbeitbar
                )

        # Erste Maske (URL / Login)
        schema = vol.Schema(
            {
                vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_subjects(self, user_input: dict[str, Any] | None = None):
        """Zweiter Schritt: erkannte Fächer benennen."""
        assert self._staged_data is not None

        if user_input is not None:
            subject_map = {k[4:]: v for k, v in user_input.items() if k.startswith("map_")}
            return self.async_create_entry(
                title=self._staged_data[CONF_USERNAME],
                data=self._staged_data,
                options={"subject_map": subject_map},
            )

        defaults = {}
        for s in self._detected_subjects:
            defaults[f"map_{s}"] = SUBJECT_DEFAULTS.get(s, s.upper())

        schema = vol.Schema({vol.Optional(k, default=v): str for k, v in defaults.items()})

        return self.async_show_form(
            step_id="subjects",
            data_schema=schema,
            description_placeholders={
                "hint": "Sprechende Namen für erkannte Fächer vergeben (später unter Optionen änderbar)."
            },
        )

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Options-Flow: Fächer-Mapping (de -> Deutsch, ...), falls man später ändern will."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            subject_map = {k[4:]: v for k, v in user_input.items() if k.startswith("map_")}
            return self.async_create_entry(title="", data={"subject_map": subject_map})

        # Fächer ermitteln – live aus Coordinator, sonst aus last.json
        subjects: list[str] = []
        data_bucket = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
        coordinator = data_bucket.get("coordinator")
        grades = (getattr(coordinator, "data", None) or {}).get("grades") if coordinator else None
        if grades:
            subjects = sorted(grades.keys())

        if not subjects:
            path = self.hass.config.path(STORE_DIR, STORE_LAST_JSON)
            try:
                raw = await self.hass.async_add_executor_job(Path(path).read_text, "utf-8")
                j = json.loads(raw)
                subjects = sorted((j.get("grades") or {}).keys())
            except Exception:
                subjects = []

        defaults = (self.entry.options or {}).get("subject_map") or {}
        if subjects:
            schema = vol.Schema(
                {vol.Optional(f"map_{s}", default=defaults.get(s, SUBJECT_DEFAULTS.get(s, s.upper()))): str
                 for s in subjects}
            )
        else:
            schema = vol.Schema(
                {vol.Optional("Hinweis", default="Noch keine Fächer gefunden – bitte später erneut öffnen."): str}
            )

        return self.async_show_form(step_id="init", data_schema=schema)
