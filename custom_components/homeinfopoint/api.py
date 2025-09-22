from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional
from urllib.parse import urljoin

import aiohttp
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

START_PATH = "/"                # Startseite
LOGIN_PATH = "/login.php"       # Login-Ziel
AFTER_LOGIN_PATH = "/getdata.php"  # Seite mit Daten


class CannotConnect(Exception):
    pass


class InvalidAuth(Exception):
    pass


class HomeInfoPointClient:
    """HTTP-Client pro ConfigEntry (eigene Session & Cookies)."""

    def __init__(self, hass, base_url: str) -> None:
        self._hass = hass
        self._base_url = base_url.rstrip("/") + "/"
        self._cookie_jar = aiohttp.CookieJar(unsafe=True)
        self._session = async_create_clientsession(hass, cookie_jar=self._cookie_jar)
        self._logged_in = False

    async def async_close(self) -> None:
        try:
            await self._session.close()
        except Exception:
            pass

    async def async_login(self, username: str, password: str) -> None:
        """Zweistufiger Login mit kurzem Retry, bis Session aktiv ist."""
        await self._get(START_PATH, force_nocache=True)
        await self._post(LOGIN_PATH, data={"username": username, "password": password, "login": "Anmelden"})

        # Warte kurz, bis Server die Session aktiviert hat
        delays = [0.2, 0.6, 1.0]
        for d in delays + [0]:
            html = await self._get(AFTER_LOGIN_PATH)
            if not _looks_like_login_page(html):
                self._logged_in = True
                return
            if d:
                _LOGGER.debug("Login noch nicht aktiv – Retry in %.1fs …", d)
                await asyncio.sleep(d)

        # Fallback: Startseite frisch + Login nochmal
        await self._get(START_PATH, force_nocache=True)
        await self._post(LOGIN_PATH, data={"username": username, "password": password, "login": "Anmelden"})
        html = await self._get(AFTER_LOGIN_PATH)
        if _looks_like_login_page(html):
            raise InvalidAuth("Login nicht akzeptiert (weiterhin Login-Form sichtbar).")
        self._logged_in = True

    async def async_login_and_fetch_html(self) -> str:
        """Nach erfolgreichem Login die Daten-Seite holen (HTML zurück)."""
        html = await self._get(AFTER_LOGIN_PATH)
        if _looks_like_login_page(html):
            raise InvalidAuth("Nach Login weiterhin Login-Seite angezeigt.")
        return html

    async def async_fetch_data_html(self) -> str:
        """Aktuelle Daten-Seite holen (setzt gültige Session voraus)."""
        html = await self._get(AFTER_LOGIN_PATH, force_nocache=True)
        if _looks_like_login_page(html):
            # Session abgelaufen
            raise InvalidAuth("Session abgelaufen – Login erforderlich.")
        return html

    # ------------- interne HTTP-Helfer -------------

    async def _get(self, path: str, *, force_nocache: bool = False) -> str:
        url = urljoin(self._base_url, path.lstrip("/"))
        params = {"_": int(time.time())} if force_nocache else None
        headers = {"Cache-Control": "no-cache", "Pragma": "no-cache"} if force_nocache else None
        try:
            async with self._session.get(url, params=params, headers=headers, allow_redirects=True) as resp:
                text = await resp.text()
                _LOGGER.debug("HIP: GET %s -> %s", url, resp.status)
                if resp.status >= 500:
                    raise CannotConnect(f"Serverfehler {resp.status}")
                return text
        except aiohttp.ClientError as e:
            raise CannotConnect(str(e)) from e

    async def _post(self, path: str, *, data: dict) -> str:
        url = urljoin(self._base_url, path.lstrip("/"))
        try:
            async with self._session.post(url, data=data, allow_redirects=True) as resp:
                text = await resp.text()
                _LOGGER.debug("HIP: POST %s -> %s", url, resp.status)
                if resp.status in (401, 403):
                    raise InvalidAuth("HTTP auth error")
                if resp.status >= 500:
                    raise CannotConnect(f"Serverfehler {resp.status}")
                return text
        except aiohttp.ClientError as e:
            raise CannotConnect(str(e)) from e


def _looks_like_login_page(html: str) -> bool:
    """Heuristik: Loginformular vorhanden?"""
    h = (html or "").lower()
    return ('name="username"' in h and 'name="password"' in h) or "login.php" in h
