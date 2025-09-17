from __future__ import annotations

import asyncio
import logging
import re
from typing import Tuple

from aiohttp import ClientError
from yarl import URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import LOGIN_PATH, AFTER_LOGIN_PATH

_LOGGER = logging.getLogger(__name__)

# Login-Form zuverlässig erkennen (entweder login.php-Form oder Passwortfeld)
LOGIN_FORM_RE = re.compile(
    r"(?:<form[^>]+action=[\"']?login\.php[\"']?[^>]*>)|(?:<input[^>]+type=[\"']?password[\"']?[^>]*>)",
    re.IGNORECASE | re.DOTALL,
)

# kurze Retries nach dem Login, weil das Backend offenbar „warm werden“ muss
RETRY_DELAYS_PRIMARY = [0.2, 0.6, 1.0]
RETRY_DELAYS_FALLBACK = [0.3, 0.8, 1.2, 2.0]


class CannotConnect(Exception):
    """Keine Verbindung möglich / unerwarteter HTTP-Status."""


class InvalidAuth(Exception):
    """Login fehlgeschlagen (falsche Daten / Session nicht authentifiziert)."""


class HomeInfoPointClient:
    """HTTP-Client für Formular-Login + Abruf einer Zielseite."""

    def __init__(self, hass, base_url: str) -> None:
        base = URL(base_url.strip())
        if not base.path.endswith("/"):
            base = base.with_path(base.path + "/")
        self._base = base

        self._hass = hass
        # Gemeinsame HA-Session inkl. CookieJar (wichtig für PHPSESSID)
        self._session = async_get_clientsession(hass)
        # Browser-nahe Header
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        }
        self._nocache = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        }

    # -------- Kern-Helfer, den wir im Flow/Setup verwenden --------
    async def async_login_and_fetch_html(self) -> str:
        """
        Führt einen Login durch und holt danach getdata.php.
        Wenn die Session noch nicht „aktiv“ ist, wird einmal ein
        kompletter zweiter Login durchgeführt (Fallback), jeweils mit Retries.
        """
        # 1) Erster Login
        await self._login_once()
        html = await self._fetch_after_login_with_retries(RETRY_DELAYS_PRIMARY)
        if not self._looks_like_login_form(html):
            return html

        _LOGGER.debug("HIP: Nach erstem Login weiterhin Login-Formular sichtbar – starte Fallback-Login.")
        # 2) Fallback: Startseite neu laden + zweiter Login
        await self._login_once(force_refresh=True)
        html = await self._fetch_after_login_with_retries(RETRY_DELAYS_FALLBACK)
        if self._looks_like_login_form(html):
            _LOGGER.debug("HIP: Auch nach Fallback-Login weiterhin Login-Formular (Snippet): %s", html[:300])
            raise InvalidAuth("Login nicht erfolgreich (Login-Formular erneut).")
        return html

    # -------- Einzel-Schritte (GET/POST/GET) --------
    async def _login_once(self, force_refresh: bool = False) -> None:
        """GET Startseite (optional no-cache), dann POST login.php."""
        start_url = self._base
        login_url = self._base / LOGIN_PATH

        try:
            # 1) Startseite -> Session/Cookies
            headers = {**self._headers, **(self._nocache if force_refresh else {})}
            _LOGGER.debug("HIP: GET %s (force_refresh=%s)", start_url, force_refresh)
            async with self._session.get(start_url, headers=headers, timeout=20) as resp:
                _LOGGER.debug("HIP: GET %s -> %s", start_url, resp.status)
                if resp.status not in (200, 302):
                    text = await resp.text()
                    raise CannotConnect(f"GET start {resp.status}: {text[:200]}")

            # 2) Formular-POST
            data = {"username": "", "password": "", "login": "Anmelden"}  # Defaults
            # Wir überschreiben direkt davor – aber falls jemand falsche Reihenfolge ändert:
            data["username"] = self._username_placeholder or ""  # nur zur Sicherheit
            data["password"] = self._password_placeholder or ""

        except AttributeError:
            # Falls Placeholders noch nicht gesetzt (normaler Pfad: _login_once wird über async_login* aufgerufen)
            pass

        try:
            # echte Felder setzen
            data = {"username": self._username_placeholder, "password": self._password_placeholder, "login": "Anmelden"}
            headers = {
                **self._headers,
                "Referer": str(start_url),
                "Origin": f"{self._base.scheme}://{self._base.host}",
                "Content-Type": "application/x-www-form-urlencoded",
            }

            _LOGGER.debug("HIP: POST %s (username=%s)", login_url, self._username_placeholder)
            async with self._session.post(
                login_url, data=data, headers=headers, timeout=20, allow_redirects=True
            ) as resp:
                _LOGGER.debug("HIP: POST %s -> %s", login_url, resp.status)
                if resp.status not in (200, 302):
                    text = await resp.text()
                    raise CannotConnect(f"POST login {resp.status}: {text[:200]}")

            # Debug: Cookies nach Login
            jar = self._session.cookie_jar.filter_cookies(str(self._base))
            _LOGGER.debug("HIP: Cookies nach Login: %s", {k: v.value for k, v in jar.items()})

        except ClientError as err:
            _LOGGER.exception("HIP: ClientError bei Login: %s", err)
            raise CannotConnect(str(err)) from err
        except Exception as err:
            _LOGGER.exception("HIP: Unerwarteter Fehler bei Login: %s", err)
            raise CannotConnect(str(err)) from err

    async def _fetch_after_login_once(self) -> Tuple[int, str]:
        """Einzelner GET auf getdata.php."""
        url = self._base / AFTER_LOGIN_PATH
        async with self._session.get(
            url,
            headers={**self._headers, **self._nocache, "Referer": str(self._base)},
            timeout=20,
            allow_redirects=True,
        ) as resp:
            status = resp.status
            html = await resp.text()
            _LOGGER.debug("HIP: GET %s -> %s", url, status)
            return status, html

    async def _fetch_after_login_with_retries(self, delays: list[float]) -> str:
        """Rufe getdata.php auf, mit mehreren Versuchen und Wartezeiten."""
        status, html = await self._fetch_after_login_once()
        attempt = 0
        while status == 200 and self._looks_like_login_form(html) and attempt < len(delays):
            delay = delays[attempt]
            _LOGGER.debug("HIP: Login noch nicht 'aktiv' – Retry in %.1fs …", delay)
            await asyncio.sleep(delay)
            status, html = await self._fetch_after_login_once()
            attempt += 1

        if status != 200:
            raise CannotConnect(f"GET after-login {status}: {html[:200]}")

        # Cookies vor Rückgabe loggen (Debug)
        jar = self._session.cookie_jar.filter_cookies(str(self._base))
        _LOGGER.debug("HIP: Cookies vor return: %s", {k: v.value for k, v in jar.items()})
        return html

    @staticmethod
    def _looks_like_login_form(html: str) -> bool:
        """Nur echte Login-Formulare erkennen (login.php oder Passwortfeld)."""
        return bool(LOGIN_FORM_RE.search(html))

    # ---------- Public Convenience (zum Setzen der Anmeldedaten) ----------
    async def async_login(self, username: str, password: str) -> None:
        """Nur Platzhalter setzen, damit _login_once sie nutzen kann (alte API-Kompatibilität)."""
        self._username_placeholder = username
        self._password_placeholder = password
        # Kein unmittelbarer Login hier – der neue Flow nutzt async_login_and_fetch_html()
        return
