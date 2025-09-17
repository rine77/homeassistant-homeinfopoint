from __future__ import annotations

from bs4 import BeautifulSoup
from typing import Any, Dict, List, Tuple
import re

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _headers(ths) -> List[str]:
    return [_norm(th.get_text()) for th in ths]

def _table_rows(table) -> List[List[str]]:
    rows: List[List[str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        rows.append([_norm(c.get_text(separator=" ").strip()) for c in cells])
    return rows

def parse(html: str) -> Dict[str, Any]:
    """
    Liefert:
    {
      "student": {"name": "...", "klasse": "..."},
      "grades": { "de": [ { "Datum": "...", "Zensur": "...", ... } ], ...},
      "homework": [ { "Datum": "...", "Fach": "...", "Hausaufgaben": "..." }, ... ],
      "remarks":  [ { "Datum": "...", "Typ": "...", "Stunde": "...", "Bemerkung": "..." }, ... ]
    }
    """
    soup = BeautifulSoup(html, "html.parser")

    # --- Name/Klasse: heuristisch z.B. in Kopf-/Info-Panel suchen ---
    name = ""
    klasse = ""
    # Häufig stehen Name/Klasse in einem <div class="panel-hint"> oder Überschriften/strong
    info_text = " ".join(_norm(x.get_text()) for x in soup.select(".panel-hint, #content h1, #content h2, #content h3"))
    # Beispiele: "Max Mustermann (Klasse 7b)" oder "Klasse: 7b"
    m = re.search(r"klasse[:\s]+([A-Za-z0-9\-_/]+)", info_text, re.IGNORECASE)
    if m:
        klasse = m.group(1)
    # Name: nimm die erste Kombination von zwei Wörtern mit Großbuchstaben als Heuristik, falls vorhanden
    n = re.search(r"\b([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)+)\b", info_text)
    if n:
        name = n.group(1)

    # --- Tabellen sammeln ---
    tables = soup.find_all("table")
    grades_by_subject: Dict[str, List[Dict[str, str]]] = {}
    homework: List[Dict[str, str]] = []
    remarks: List[Dict[str, str]] = []

    for table in tables:
        rows = _table_rows(table)
        if not rows or len(rows) < 2:
            continue
        head = [h.lower() for h in rows[0]]

        # Notentabelle je Fach: Datum, Zensur, Bemerkung, Teilnote / Wichtung, Halbjahr
        if {"datum", "zensur"}.issubset(set(h.replace("teilnote / wichtung", "teilnote / wichtung") for h in head)):
            # Fach aus Überschrift über der Tabelle erraten (z.B. <h2>Deutsch</h2>)
            subject = _find_subject_for_table(table)
            if not subject:
                subject = "unbekannt"
            # Baue Dict pro Zeile nach Spaltennamen
            for r in rows[1:]:
                entry = _row_to_entry(rows[0], r)
                grades_by_subject.setdefault(subject_key(subject), []).append(entry)
            continue

        # Hausaufgaben: Datum, Fach, Hausaufgaben
        if {"datum", "fach", "hausaufgaben"}.issubset(set(head)):
            for r in rows[1:]:
                homework.append(_row_to_entry(rows[0], r))
            continue

        # Bemerkungen: Datum, Typ, Stunde, Bemerkung
        if {"datum", "typ", "stunde", "bemerkung"}.issubset(set(head)):
            for r in rows[1:]:
                remarks.append(_row_to_entry(rows[0], r))
            continue

    return {
        "student": {"name": name, "klasse": klasse},
        "grades": grades_by_subject,
        "homework": homework,
        "remarks": remarks,
    }

def subject_key(subject: str) -> str:
    """z.B. 'Deutsch' -> 'de', 'Mathematik' -> 'ma' (Heuristik), sonst lower slug."""
    s = subject.strip().lower()
    mapping = {
        "deutsch": "de",
        "mathematik": "ma",
        "englisch": "en",
        "physik": "ph",
        "chemie": "ch",
        "biologie": "bio",
        "geschichte": "ge",
        "erdkunde": "ek",
        "sport": "sp",
        "musik": "mu",
        "kunst": "ku",
        "informatik": "inf",
        "französisch": "fr",
        "latein": "la",
    }
    if s in mapping:
        return mapping[s]
    # letzte Notlösung: Kürzel aus ersten 2 Buchstaben
    return re.sub(r"[^a-z0-9]+", "", s)[:3] or "fach"

def _find_subject_for_table(table) -> str:
    # Blick nach oben: nächstes vorheriges H2/H3 strong als Fach
    cur = table
    for _ in range(5):
        cur = cur.previous_sibling or cur.parent
        if not cur:
            break
        if getattr(cur, "name", None) in ("h1", "h2", "h3", "strong"):
            txt = _norm(cur.get_text())
            if txt:
                return txt
    return ""

def _row_to_entry(headers: list[str], values: list[str]) -> Dict[str, str]:
    entry: Dict[str, str] = {}
    for i, h in enumerate(headers):
        key = _norm(h)
        val = values[i] if i < len(values) else ""
        entry[key] = _norm(val)
    return entry
