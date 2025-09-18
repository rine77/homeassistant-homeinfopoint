from __future__ import annotations

from bs4 import BeautifulSoup
from typing import Any, Dict, List
import re

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def _headers(ths) -> List[str]:
    return [_norm(th.get_text()) for th in ths]

def _table_rows(table) -> List[List[str]]:
    """
    Liefert alle Zeilen einer Tabelle (inkl. Header).
    Wichtig: Auch wenn es NUR eine Headerzeile gibt, geben wir diese zurück,
    damit wir das Fach registrieren können (leere Tabelle).
    """
    rows: List[List[str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        rows.append([_norm(c.get_text(separator=" ").strip()) for c in cells])
    return rows

def parse(html: str) -> Dict[str, Any]:
    """
    Ergebnis-Struktur:
    {
      "student": {"name": "...", "klasse": "..."},
      "grades":  { "de": [ { "Datum": "...", "Zensur": "...", ... } ], ... },  # ggf. leere Listen
      "homework": [ { "Datum": "...", "Fach": "...", "Hausaufgaben": "..." }, ... ],
      "remarks":  [ { "Datum": "...", "Typ": "...", "Stunde": "...", "Bemerkung": "..." }, ... ]
    }
    """
    soup = BeautifulSoup(html, "html.parser")

    # --- Name/Klasse heuristisch ---
    name = ""
    klasse = ""
    info_text = " ".join(_norm(x.get_text()) for x in soup.select(".panel-hint, #content h1, #content h2, #content h3"))
    m = re.search(r"klasse[:\s]+([A-Za-z0-9\-_/]+)", info_text, re.IGNORECASE)
    if m:
        klasse = m.group(1)
    n = re.search(r"\b([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)+)\b", info_text)
    if n:
        name = n.group(1)

    # --- Tabellen scannen ---
    tables = soup.find_all("table")
    grades_by_subject: Dict[str, List[Dict[str, str]]] = {}
    homework: List[Dict[str, str]] = []
    remarks: List[Dict[str, str]] = []

    for table in tables:
        rows = _table_rows(table)
        if not rows:
            continue
        head_raw = rows[0]
        head = [h.lower() for h in head_raw]

        # Noten-Tabellen erkennen an Spalten-Headern
        # Wir akzeptieren Tabellen auch dann, wenn sie NUR die Headerzeile haben.
        is_grade_table = "datum" in head and "zensur" in head
        if is_grade_table:
            subject = _find_subject_for_table(table) or "unbekannt"
            key = subject_key(subject)
            grades_by_subject.setdefault(key, [])  # <-- Fach wird auch ohne Zeilen registriert

            # Datenzeilen (falls vorhanden) ab Zeile 1
            if len(rows) > 1:
                for r in rows[1:]:
                    entry = _row_to_entry(head_raw, r)
                    grades_by_subject[key].append(entry)
            continue

        # Hausaufgaben: Datum, Fach, Hausaufgaben
        if {"datum", "fach", "hausaufgaben"}.issubset(set(head)):
            for r in rows[1:]:
                homework.append(_row_to_entry(head_raw, r))
            continue

        # Bemerkungen: Datum, Typ, Stunde, Bemerkung
        if {"datum", "typ", "stunde", "bemerkung"}.issubset(set(head)):
            for r in rows[1:]:
                remarks.append(_row_to_entry(head_raw, r))
            continue

    return {
        "student": {"name": name, "klasse": klasse},
        "grades": grades_by_subject,
        "homework": homework,
        "remarks": remarks,
    }

def subject_key(subject: str) -> str:
    """z.B. 'Deutsch' -> 'de', 'Mathematik' -> 'ma', sonst schlanker Slug."""
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
    # Fallback: auf 3 Zeichen eingedampft
    slug = re.sub(r"[^a-z0-9]+", "", s)
    return (slug[:3] or "fach")

def _find_subject_for_table(table) -> str:
    """
    Sucht die Überschrift (h1/h2/h3/strong) oberhalb/um die Tabelle herum,
    um den Fachnamen zu ermitteln.
    """
    # 1) Direkt vorherige Geschwister
    cur = table
    for _ in range(6):
        cur = getattr(cur, "previous_sibling", None)
        if not cur:
            break
        if getattr(cur, "name", None) in ("h1", "h2", "h3", "strong"):
            txt = _norm(cur.get_text())
            if txt:
                return txt

    # 2) Elter-Elemente hochlaufen
    cur = table
    for _ in range(4):
        cur = getattr(cur, "parent", None)
        if not cur:
            break
        # Überschrift innerhalb des Eltern-Elements
        heading = cur.find(["h1", "h2", "h3", "strong"])
        if heading:
            txt = _norm(heading.get_text())
            if txt:
                return txt

    return ""

def _row_to_entry(headers: List[str], values: List[str]) -> Dict[str, str]:
    entry: Dict[str, str] = {}
    for i, h in enumerate(headers):
        key = _norm(h)
        val = values[i] if i < len(values) else ""
        entry[key] = _norm(val)
    return entry
