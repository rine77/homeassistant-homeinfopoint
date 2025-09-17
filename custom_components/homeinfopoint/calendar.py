# custom_components/homeinfopoint/calendar.py
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, List

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# dd.mm.yyyy Tokens
_DMY_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")


def _extract_dates_de(s: str) -> list[date]:
    """Alle Datumsangaben im Format dd.mm.yyyy extrahieren."""
    out: list[date] = []
    s = (s or "").strip()
    for d, m, y in _DMY_RE.findall(s):
        try:
            out.append(date(int(y), int(m), int(d)))
        except Exception:
            continue
    return out


def _parse_due_date_de(s: str) -> date | None:
    """Fälligkeit = spätestes Datum im Text (z. B. '16.09.2025 zum 18.09.2025' -> 18.09.2025)."""
    ds = _extract_dates_de(s)
    return max(ds) if ds else None


def _parse_single_date_de(s: str) -> date | None:
    """Erstes Datum im Text (für Bemerkungen mit nur einem Datum)."""
    ds = _extract_dates_de(s)
    return ds[0] if ds else None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    async_add_entities(
        [
            HIPHomeworkCalendar(coordinator, entry),
            HIPRemarksCalendar(coordinator, entry),
        ]
    )


class _HIPBaseCalendar(CoordinatorEntity, CalendarEntity):
    """Gemeinsame Basis für die beiden Kalender-Entities."""

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Home.InfoPoint ({entry.title})",
            manufacturer="RHC GmbH",
            model="Home.InfoPoint",
        )
        self._event: CalendarEvent | None = None
        self._recompute_event()

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    # CalendarEntity: State-Event
    @property
    def event(self) -> CalendarEvent | None:
        return self._event

    def _handle_coordinator_update(self) -> None:
        self._recompute_event()
        super()._handle_coordinator_update()
        self.async_write_ha_state()

    async def async_get_events(self, hass: HomeAssistant, start_date, end_date) -> List[CalendarEvent]:
        events = self._build_all_events()
        # Filter auf Zeitfenster (ganztägige Events: start/end sind date, end exklusiv)
        def in_range(ev: CalendarEvent) -> bool:
            ev_start = ev.start  # type: ignore[assignment]
            ev_end = ev.end      # type: ignore[assignment]
            return not (ev_end <= start_date.date() or ev_start >= end_date.date())
        return [ev for ev in events if in_range(ev)]

    def _recompute_event(self) -> None:
        events = self._build_all_events()
        today = dt_util.now().date()
        upcoming = sorted(
            (ev for ev in events if ev.start >= today),  # type: ignore[operator]
            key=lambda ev: (ev.start, ev.summary),       # type: ignore[arg-type]
        )
        self._event = upcoming[0] if upcoming else None

    # Muss in den Subklassen implementiert werden
    def _build_all_events(self) -> List[CalendarEvent]:
        raise NotImplementedError


class HIPHomeworkCalendar(_HIPBaseCalendar):
    """Kalender: Hausaufgaben am Fälligkeitsdatum (spätestes Datum im Feld 'Datum')."""

    _attr_has_entity_name = True
    _attr_name = "Hausaufgaben Kalender"
    _attr_icon = "mdi:calendar-text"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}-homework-calendar"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {"total_homework": len(data.get("homework") or [])}

    def _build_all_events(self) -> List[CalendarEvent]:
        data = self.coordinator.data or {}
        hw = data.get("homework") or []
        events: List[CalendarEvent] = []

        for item in hw:
            datum = item.get("Datum") or item.get("datum") or ""
            fach = item.get("Fach") or item.get("fach") or ""
            text = item.get("Hausaufgaben") or item.get("hausaufgaben") or ""

            due = _parse_due_date_de(datum)  # spätestes Datum im Feld
            if not due:
                _LOGGER.debug("Kalender(HW): kein Datum in '%s' (Fach=%s, Text=%s)", datum, fach, text)
                continue

            start = due
            end = due + timedelta(days=1)
            summary = f"{fach}: {text}".strip(": ")
            events.append(CalendarEvent(summary=summary, start=start, end=end, description=text))

        _LOGGER.debug("Kalender(HW): %d Events gebaut", len(events))
        return events


class HIPRemarksCalendar(_HIPBaseCalendar):
    """Kalender: Bemerkungen als ganztägiges Event am Datum (erste DD.MM.YYYY in 'Datum')."""

    _attr_has_entity_name = True
    _attr_name = "Bemerkungen Kalender"
    _attr_icon = "mdi:calendar-alert"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}-remarks-calendar"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {"total_remarks": len(data.get("remarks") or [])}

    def _build_all_events(self) -> List[CalendarEvent]:
        data = self.coordinator.data or {}
        rm = data.get("remarks") or []
        events: List[CalendarEvent] = []

        for item in rm:
            datum = item.get("Datum") or item.get("datum") or ""
            typ = item.get("Typ") or item.get("typ") or ""
            stunde = item.get("Stunde") or item.get("stunde") or ""
            bem = item.get("Bemerkung") or item.get("bemerkung") or ""

            day = _parse_single_date_de(datum)  # erste DD.MM.YYYY
            if not day:
                _LOGGER.debug("Kalender(Bem): kein Datum in '%s' (Typ=%s)", datum, typ)
                continue

            start = day
            end = day + timedelta(days=1)
            suffix = f" (Stunde {stunde})" if stunde else ""
            summary = f"{typ}{suffix}".strip()
            description = bem or "Bemerkung"
            events.append(CalendarEvent(summary=summary, start=start, end=end, description=description))

        _LOGGER.debug("Kalender(Bem): %d Events gebaut", len(events))
        return events
