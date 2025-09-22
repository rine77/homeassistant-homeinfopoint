# custom_components/homeinfopoint/config_flow.py
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.selector import selector

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
from .parser import parse as parse_html

# sinnvolle Defaults für Fachkürzel -> Klartext
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
    """Konfigurations-Flow für Home.InfoPoint."""

    VERSION = 1

    _staged_data: dict[str, Any] | None = None
    _detected_subjects: list[str] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Erster Schritt: Zugangsdaten prüfen und ggf. Fächer entdecken."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input[CONF_BASE_URL] = _normalize_base_url(user_input[CONF_BASE_URL])

            # Eintrag eindeutig machen: base_url::username
            unique_id = f"{user_input[CONF_BASE_URL]}::{user_input[CONF_USERNAME]}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            # Login & erste Seite laden
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
                # Fächer erkennen (falls schon Notentabellen existieren)
                try:
                    parsed = parse_html(html)
                    grades = parsed.get("grades") or {}
                    self._detected_subjects = sorted(grades.keys())
                except Exception:
                    self._detected_subjects = []

                self._staged_data = {
                    CONF_BASE_URL: user_input[CONF_BASE_URL],
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                }

                if self._detected_subjects:
                    return await self.async_step_subjects()

                # Keine Fächer gefunden -> Entry sofort anlegen, Optionen später
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data=self._staged_data,
                    options={},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_subjects(self, user_input: dict[str, Any] | None = None):
        """Zweiter Schritt: Fächer benennen + optional WebUntis-Unterrichtskalender auswählen."""
        assert self._staged_data is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            subject_map = {k[4:]: v for k, v in user_input.items() if k.startswith("map_")}
            subject_match = {k[6:]: v for k, v in user_input.items() if k.startswith("match_") and v.strip()}
            lessons_cal = (user_input.get("webuntis_lessons_calendar") or "").strip()

            return self.async_create_entry(
                title=self._staged_data[CONF_USERNAME],
                data=self._staged_data,
                options={
                    "subject_map": subject_map,
                    "subject_match_map": subject_match,
                    "webuntis_lessons_calendar": lessons_cal,
                },
            )

        subjects = list(self._detected_subjects or [])
        defaults_map = {f"map_{s}": SUBJECT_DEFAULTS.get(s, s.upper()) for s in subjects}
        defaults_match = {f"match_{s}": f"{SUBJECT_DEFAULTS.get(s, s.upper())}|{s}" for s in subjects}

        # Schema IMMER definieren (auch wenn subjects leer ist)
        schema_dict: dict = {
            vol.Optional("webuntis_lessons_calendar", default=""): selector(
                {
                    "entity": {
                        "domain": "calendar",
                        # Wenn deine WebUntis-Integration anders heißt, den Filter entfernen/ändern:
                        "integration": "webuntis",
                    }
                }
            ),
        }
        schema_dict.update({vol.Optional(k, default=v): str for k, v in defaults_map.items()})
        schema_dict.update({vol.Optional(k, default=v): str for k, v in defaults_match.items()})

        schema = vol.Schema(schema_dict)
        return self.async_show_form(step_id="subjects", data_schema=schema, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Options-Flow: Fächer/Kalender anpassen."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            subject_map = {k[4:]: v for k, v in user_input.items() if k.startswith("map_")}
            subject_match = {k[6:]: v for k, v in user_input.items() if k.startswith("match_") and v.strip()}
            lessons_cal = (user_input.get("webuntis_lessons_calendar") or "").strip()
            return self.async_create_entry(
                title="",
                data={
                    "subject_map": subject_map,
                    "subject_match_map": subject_match,
                    "webuntis_lessons_calendar": lessons_cal,
                },
            )

        # Fächer aus aktuellen Daten oder aus last.json (entry-spezifisch) ermitteln
        subjects: list[str] = []
        data_bucket = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
        coordinator = data_bucket.get("coordinator")
        grades = (getattr(coordinator, "data", None) or {}).get("grades") if coordinator else None
        if grades:
            subjects = sorted(grades.keys())
        if not subjects:
            path = self.hass.config.path(STORE_DIR, self.entry.entry_id, STORE_LAST_JSON)
            try:
                raw = await self.hass.async_add_executor_job(Path(path).read_text, "utf-8")
                j = json.loads(raw)
                subjects = sorted((j.get("grades") or {}).keys())
            except Exception:
                subjects = []

        opts = self.entry.options or {}
        defaults_map = (opts.get("subject_map") or {})
        defaults_match = (opts.get("subject_match_map") or {})
        lessons_cal = (opts.get("webuntis_lessons_calendar") or "")

        if subjects:
            schema = vol.Schema(
                {
                    vol.Optional("webuntis_lessons_calendar", default=lessons_cal): selector(
                        {
                            "entity": {
                                "domain": "calendar",
                                "integration": "webuntis",
                            }
                        }
                    ),
                    **{
                        vol.Optional(f"map_{s}", default=defaults_map.get(s, SUBJECT_DEFAULTS.get(s, s.upper()))): str
                        for s in subjects
                    },
                    **{
                        vol.Optional(
                            f"match_{s}",
                            default=defaults_match.get(s, f"{defaults_map.get(s, SUBJECT_DEFAULTS.get(s, s.upper()))}|{s}"),
                        ): str
                        for s in subjects
                    },
                }
            )
        else:
            # Fallback-Form, falls noch nichts erkannt wurde
            schema = vol.Schema(
                {
                    vol.Optional("webuntis_lessons_calendar", default=lessons_cal): selector(
                        {"entity": {"domain": "calendar", "integration": "webuntis"}}
                    )
                }
            )

        return self.async_show_form(step_id="init", data_schema=schema)
