# custom_components/homeinfopoint/sensor.py
from __future__ import annotations

from typing import Any, Dict, List, Set
import re

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data_bucket = hass.data[DOMAIN][entry.entry_id]
    coordinator = data_bucket["coordinator"]

    entities: List[SensorEntity] = []

    # NEU: Schüler-Info-Sensor (Name/Klasse)
    entities.append(HIPStudentInfoSensor(coordinator, entry))

    # Pro Fach je ein Sensor (Durchschnitt 1–6, „-“ ignoriert)
    created: Set[str] = set()
    grades: Dict[str, List[dict]] = (coordinator.data or {}).get("grades") or {}
    for subject_key in sorted(grades.keys()):
        entities.append(HIPSubjectGradesSensor(coordinator, entry, subject_key))
        created.add(subject_key)

    async_add_entities(entities)

    # Später neu erkannte Fächer dynamisch nachlegen
    @callback
    def _maybe_add_new_subjects() -> None:
        current_grades: Dict[str, List[dict]] = (coordinator.data or {}).get("grades") or {}
        new_subjects = sorted(set(current_grades.keys()) - created)
        if not new_subjects:
            return
        async_add_entities([HIPSubjectGradesSensor(coordinator, entry, s) for s in new_subjects])
        created.update(new_subjects)

    entry.async_on_unload(coordinator.async_add_listener(_maybe_add_new_subjects))


# ----------------- Schüler-Info -----------------

class HIPStudentInfoSensor(CoordinatorEntity, SensorEntity):
    """Sensor mit Name (State) und Klasse (Attribute)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:account-school"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        # Anzeigename stabil halten (State ändert sich bei neuen Daten ohnehin)
        self._attr_name = "Schüler"
        self._attr_unique_id = f"{entry.entry_id}-student-info"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Home.InfoPoint ({entry.title})",
            manufacturer="RHC GmbH",
            model="Home.InfoPoint",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> Any:
        student = (self.coordinator.data or {}).get("student") or {}
        name = (student.get("name") or "").strip()
        # Falls Parser (noch) keinen Namen fand, auf den Entry-Titel fallen
        return name or self._entry.title

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        student = (self.coordinator.data or {}).get("student") or {}
        klasse = (student.get("klasse") or "").strip()
        return {
            "klasse": klasse,
            "student_name": (student.get("name") or "").strip(),
            # ein paar hilfreiche Zusatzinfos:
            "subject_count": len(((self.coordinator.data or {}).get("grades") or {})),
            "homework_count": len(((self.coordinator.data or {}).get("homework") or [])),
            "remarks_count": len(((self.coordinator.data or {}).get("remarks") or [])),
        }


# ----------------- Fach-Sensoren (Durchschnitt) -----------------

class HIPSubjectGradesSensor(CoordinatorEntity, SensorEntity):
    """Pro Fach ein Sensor. State = Durchschnitt (nur 1–6), Attribute = Einträge (Datum/Zensur/Bemerkung)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:book-open-variant"

    def __init__(self, coordinator, entry: ConfigEntry, subject_key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._subject_key = subject_key

        mapping = (entry.options or {}).get("subject_map") or {}
        friendly = mapping.get(subject_key, subject_key.upper())

        self._attr_name = f"{friendly}"
        self._attr_unique_id = f"{entry.entry_id}-grades-{subject_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Home.InfoPoint ({entry.title})",
            manufacturer="RHC GmbH",
            model="Home.InfoPoint",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> Any:
        vals = _numeric_grades_1_to_6(self._entries)
        if not vals:
            return 0
        return round(sum(vals) / len(vals), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw_entries = self._entries
        entries = [_filter_grade_entry_minimal(e) for e in raw_entries]
        last_grade = next((e.get("Zensur") for e in reversed(entries) if e.get("Zensur")), None)
        mapping = (self._entry.options or {}).get("subject_map") or {}
        friendly = mapping.get(self._subject_key, self._subject_key.upper())
        valid_vals = _numeric_grades_1_to_6(raw_entries)

        return {
            "subject_key": self._subject_key,
            "subject_name": friendly,
            "total_entries": len(raw_entries),
            "valid_count": len(valid_vals),
            "last_grade": last_grade,
            "entries": entries,
        }

    @property
    def _entries(self) -> List[dict]:
        grades = (self.coordinator.data or {}).get("grades") or {}
        return grades.get(self._subject_key) or []


# ----------------- Helfer -----------------

_GRADE_INT_RE = re.compile(r"^[1-6]$")

def _numeric_grades_1_to_6(entries: List[dict]) -> List[int]:
    """Extrahiert nur Ganzzahlen 1..6 aus 'Zensur'."""
    vals: List[int] = []
    for e in entries:
        v = _first_of(e, ["Zensur", "zensur"])
        s = _norm(v)
        if _GRADE_INT_RE.match(s):
            vals.append(int(s))
    return vals

def _norm(s: Any) -> str:
    return " ".join(str(s or "").split()).strip()

def _first_of(d: dict, keys: List[str]) -> str:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return str(d[k])
    return ""

def _filter_grade_entry_minimal(entry: dict) -> dict:
    """Auf drei Felder eindampfen: Datum, Zensur, Bemerkung (case-insensitiv, robust)."""
    lower_map = {k.lower(): k for k in entry.keys()}

    def get_ci(wanted: str) -> str:
        k = lower_map.get(wanted.lower())
        return _norm(entry.get(k)) if k else ""

    datum = get_ci("datum")
    zensur = get_ci("zensur")
    bemerkung = get_ci("bemerkung") or get_ci("kommentar") or get_ci("note")
    return {"Datum": datum, "Zensur": zensur, "Bemerkung": bemerkung}
