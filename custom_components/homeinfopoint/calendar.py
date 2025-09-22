from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any, List, Optional, Union

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components import calendar as ha_cal
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_DMY_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")

def _extract_dates_de(s: str) -> list[date]:
    out: list[date] = []
    s = (s or "").strip()
    for d, m, y in _DMY_RE.findall(s):
        try:
            out.append(date(int(y), int(m), int(d)))
        except Exception:
            continue
    return out

def _parse_due_date_from_text(s: str) -> tuple[Optional[date], Optional[date]]:
    ds = _extract_dates_de(s)
    if not ds:
        return (None, None)
    if len(ds) == 1:
        return (ds[0], None)
    return (min(ds), max(ds))

def _as_aware(dt_d: Union[date, datetime]) -> datetime:
    tz = dt_util.now().tzinfo
    if isinstance(dt_d, date) and not isinstance(dt_d, datetime):
        return datetime.combine(dt_d, time.min, tzinfo=tz)
    if isinstance(dt_d, datetime):
        return dt_d if dt_d.tzinfo else dt_d.replace(tzinfo=tz)
    return datetime.now(tz)

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

async def _next_occurrence_matching(
    hass: HomeAssistant, lessons_calendar_entity: str, after_date: date, subject_patterns: list[str], search_days: int = 45
) -> date | None:
    start_dt = _as_aware(after_date) + timedelta(minutes=1)
    end_dt = start_dt + timedelta(days=search_days)
    try:
        events: List[CalendarEvent] = await ha_cal.async_get_events(hass, lessons_calendar_entity, start_dt, end_dt)  # type: ignore
    except Exception:
        _LOGGER.debug("WebUntis-Kalender nicht abrufbar: %s", lessons_calendar_entity, exc_info=True)
        return None

    regs = [re.compile(p, re.IGNORECASE) for p in subject_patterns if p]

    def _matches(ev: CalendarEvent) -> bool:
        title = _norm(getattr(ev, "summary", "") or "")
        if not regs:
            return True
        return any(r.search(title) for r in regs)

    def _start_date(ev: CalendarEvent) -> date:
        s = ev.start
        return s.date() if isinstance(s, datetime) else s

    cands = [ev for ev in events if _matches(ev) and _start_date(ev) >= after_date]
    if not cands:
        return None
    cands.sort(key=lambda ev: (_start_date(ev), ev.summary or ""))
    return _start_date(cands[0])


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    async_add_entities([HIPHomeworkCalendar(coordinator, entry), HIPRemarksCalendar(coordinator, entry)])


class _HIPBaseCalendar(CoordinatorEntity, CalendarEntity):
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

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def event(self) -> CalendarEvent | None:
        return self._event

    def _handle_coordinator_update(self) -> None:
        self.hass.async_create_task(self._async_recompute_event())
        super()._handle_coordinator_update()

    async def _async_recompute_event(self) -> None:
        events = await self._async_build_all_events(self.hass)
        today = dt_util.now().date()
        def _start_d(ev: CalendarEvent) -> date:
            s = ev.start
            return s if isinstance(s, date) and not isinstance(s, datetime) else s.date()
        upcoming = sorted((ev for ev in events if _start_d(ev) >= today), key=lambda ev: (_start_d(ev), ev.summary))
        self._event = upcoming[0] if upcoming else None
        self.async_write_ha_state()

    async def async_get_events(self, hass: HomeAssistant, start_date, end_date) -> List[CalendarEvent]:
        events = await self._async_build_all_events(hass)
        def in_range(ev: CalendarEvent) -> bool:
            s = ev.start if isinstance(ev.start, date) and not isinstance(ev.start, datetime) else ev.start.date()
            e = ev.end if isinstance(ev.end, date) and not isinstance(ev.end, datetime) else ev.end.date()
            return not (e <= start_date.date() or s >= end_date.date())
        return [ev for ev in events if in_range(ev)]

    async def _async_build_all_events(self, hass: HomeAssistant) -> List[CalendarEvent]:
        raise NotImplementedError


class HIPHomeworkCalendar(_HIPBaseCalendar):
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

    async def _async_build_all_events(self, hass: HomeAssistant) -> List[CalendarEvent]:
        data = self.coordinator.data or {}
        hw = data.get("homework") or []
        events: List[CalendarEvent] = []

        opts = self._entry.options or {}
        subject_map   = (opts.get("subject_map") or {})
        subject_match = (opts.get("subject_match_map") or {})
        lessons_cal   = (opts.get("webuntis_lessons_calendar") or "").strip()

        grades = (data.get("grades") or {})

        for item in hw:
            datum = (item.get("Datum") or item.get("datum") or "").strip()
            fach  = (item.get("Fach") or item.get("fach") or "").strip()
            text  = (item.get("Hausaufgaben") or item.get("hausaufgaben") or "").strip()

            assigned, due = _parse_due_date_from_text(datum)

            if not due and lessons_cal and assigned:
                # subject_key über Mapping rekonstruieren
                rev = {v: k for k, v in subject_map.items()}
                subject_key = rev.get(fach)
                if not subject_key:
                    mk = fach.lower()
                    if mk in grades:
                        subject_key = mk
                if subject_key:
                    friendly = subject_map.get(subject_key, subject_key.upper())
                    patterns = []
                    if subject_match.get(subject_key):
                        patterns.append(subject_match[subject_key])
                    patterns += [re.escape(friendly), r"\b" + re.escape(subject_key) + r"\b"]
                    due = await _next_occurrence_matching(hass, lessons_cal, assigned, patterns)

            if not due:
                _LOGGER.debug("HW: kein Fälligkeitsdatum für '%s' (Fach=%s) – übersprungen", text, fach)
                continue

            start = due
            end   = due + timedelta(days=1)
            summary = f"{fach}: {text}".strip(": ")
            events.append(CalendarEvent(summary=summary, start=start, end=end, description=text))

        _LOGGER.debug("Kalender(HW): %d Events gebaut", len(events))
        return events


class HIPRemarksCalendar(_HIPBaseCalendar):
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

    async def _async_build_all_events(self, hass: HomeAssistant) -> List[CalendarEvent]:
        data = self.coordinator.data or {}
        rm = data.get("remarks") or []
        events: List[CalendarEvent] = []

        for item in rm:
            datum = (item.get("Datum") or item.get("datum") or "").strip()
            typ   = (item.get("Typ") or item.get("typ") or "").strip()
            stunde = (item.get("Stunde") or item.get("stunde") or "").strip()
            bem   = (item.get("Bemerkung") or item.get("bemerkung") or "").strip()

            dates = _extract_dates_de(datum)
            if not dates:
                _LOGGER.debug("Bem: kein Datum in '%s' (Typ=%s)", datum, typ)
                continue
            day = dates[0]
            start = day
            end = day + timedelta(days=1)
            suffix = f" (Stunde {stunde})" if stunde else ""
            summary = f"{typ}{suffix}".strip()
            events.append(CalendarEvent(summary=summary, start=start, end=end, description=bem or "Bemerkung"))

        _LOGGER.debug("Kalender(Bem): %d Events gebaut", len(events))
        return events
