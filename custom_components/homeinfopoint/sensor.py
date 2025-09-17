# custom_components/homeinfopoint/sensor.py
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities: list[SensorEntity] = [
        HIPGradesSensor(coordinator, entry),
        HIPHomeworkSensor(coordinator, entry),
        HIPRemarksSensor(coordinator, entry),
    ]
    async_add_entities(entities)


class _HIPBaseSensor(CoordinatorEntity, SensorEntity):
    """Basisklasse für HIP-Sensoren mit gemeinsamem Device."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = None

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
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
    def _data(self) -> dict[str, Any]:
        return self.coordinator.data or {}


class HIPGradesSensor(_HIPBaseSensor):
    """Sensor für Zensuren (Noten)."""

    _attr_name = "Zensuren"
    _attr_icon = "mdi:school"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}-grades"

    @property
    def native_value(self) -> Any:
        grades = (self._data.get("grades") or {})
        total = sum(len(v or []) for v in grades.values())
        return total  # Anzahl aller Noteneinträge

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        student = self._data.get("student") or {}
        grades = self._data.get("grades") or {}
        subjects = sorted(grades.keys())
        return {
            "student": student,           # {"name": "...", "klasse": "..."}
            "subjects": subjects,         # ["de", "ma", ...]
            "by_subject": grades,         # { "de": [ { "Datum": "...", ... }, ... ], ... }
            "total_entries": self.native_value,
        }


class HIPHomeworkSensor(_HIPBaseSensor):
    """Sensor für Hausaufgaben."""

    _attr_name = "Hausaufgaben"
    _attr_icon = "mdi:notebook-edit"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}-homework"

    @property
    def native_value(self) -> Any:
        hw = self._data.get("homework") or []
        return len(hw)  # Anzahl offener/gelisteter Hausaufgaben-Einträge

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        hw = self._data.get("homework") or []
        latest = hw[0] if hw else None  # ggf. nach Datum sortieren, wenn nötig
        return {
            "entries": hw,               # [ { "Datum": "...", "Fach": "...", "Hausaufgaben": "..." }, ... ]
            "latest": latest,
            "total_entries": len(hw),
        }


class HIPRemarksSensor(_HIPBaseSensor):
    """Sensor für Bemerkungen."""

    _attr_name = "Bemerkungen"
    _attr_icon = "mdi:comment-text-multiple-outline"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}-remarks"

    @property
    def native_value(self) -> Any:
        rm = self._data.get("remarks") or []
        return len(rm)  # Anzahl Bemerkungen

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rm = self._data.get("remarks") or []
        latest = rm[0] if rm else None
        return {
            "entries": rm,               # [ { "Datum": "...", "Typ": "...", "Stunde": "...", "Bemerkung": "..." }, ... ]
            "latest": latest,
            "total_entries": len(rm),
        }
