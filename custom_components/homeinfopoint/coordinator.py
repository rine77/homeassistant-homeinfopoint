from __future__ import annotations

import json
import logging
from pathlib import Path
from datetime import timedelta
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import SCAN_INTERVAL_SECONDS, STORE_DIR, STORE_LAST_JSON
from .api import HomeInfoPointClient

_LOGGER = logging.getLogger(__name__)

class HomeInfoPointCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, client: HomeInfoPointClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="homeinfopoint",
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self._client = client

    async def _async_update_data(self):
        # Seite holen (Login inkl. Fallback ist im Client kapselt)
        html = await self._client.async_login_and_fetch_html()

        # Parsen (lazy import)
        from .parser import parse
        data = parse(html)

        # JSON ohne Import aus __init__.py schreiben → kein Zirkular-Import
        try:
            path = self.hass.config.path(STORE_DIR, STORE_LAST_JSON)
            def _write():
                p = Path(path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            await self.hass.async_add_executor_job(_write)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Konnte last.json nicht schreiben", exc_info=True)

        return data
