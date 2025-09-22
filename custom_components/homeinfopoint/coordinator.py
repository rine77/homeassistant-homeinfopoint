from __future__ import annotations

import json
import logging
from pathlib import Path
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import STORE_DIR, STORE_LAST_JSON, SCAN_INTERVAL_SECONDS
from .api import HomeInfoPointClient, InvalidAuth, CannotConnect
from .parser import parse as parse_html

_LOGGER = logging.getLogger(__name__)


class HomeInfoPointCoordinator(DataUpdateCoordinator):
    """Pollt regelmäßig die Seite, parsed HTML und liefert dict-Daten."""

    def __init__(self, hass: HomeAssistant, client: HomeInfoPointClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Home.InfoPoint Coordinator",
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.client = client
        self._entry_folder: Path | None = None  # wird in __init__.py gesetzt (optional)

    async def _async_update_data(self):
        try:
            html = await self.client.async_fetch_data_html()
        except InvalidAuth as err:
            raise UpdateFailed(str(err)) from err
        except CannotConnect as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            raise UpdateFailed(str(err)) from err

        data = parse_html(html)

        # last.json schreiben (falls Pfad gesetzt)
        if self._entry_folder:
            path = self._entry_folder / STORE_LAST_JSON
            await self.hass.async_add_executor_job(_write_text, path, json.dumps(data, ensure_ascii=False, indent=2))

        return data


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
