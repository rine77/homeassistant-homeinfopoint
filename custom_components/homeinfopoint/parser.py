# custom_components/homeinfopoint/parser.py
from __future__ import annotations

from bs4 import BeautifulSoup
from typing import Any, Dict, List, Tuple
import re

# ---------- kleine Helfer ----------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def _table_rows(table) -> List[List[str]]:
    rows: List[List[str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        rows.append([_norm(c.get_text(separator=" ").strip()) for c in cells])
    return rows

def _row_to_entry(headers: List[str], values: List[str]) -> Dict[str, str]:
    entry: Dict[str, str] = {}
    for i, h in enumerate(headers):
        key = _norm(h)
        val = values[i] if i < len(values) else ""
        entry[key] = _norm(val)
    return entry

def _find_subject_for_table(table) -> str:
    """Fächer-Überschrift (h3/strong) rund um die Notentabelle finden."""
    cur = table
    for _ in range(6):
        cur = getattr(cur, "previous_sibling", None)
        if not cur:
            break
        if getattr(cur, "name", None) in ("h1", "h2", "h3", "strong"):
            txt = _norm(cur.get_text())
            if txt:
                return txt
    cur = table
    for _ in range(4):
        cur = getattr(cur, "parent", None)
        if not cur:
            break
        heading = cur.find(["h1", "h2", "h3", "strong"])
        if heading:
            txt = _norm(heading.get_text())
            if txt:
                return txt
    return ""

def subject_key(subject: str) -> str:
    """Kürzel ableiten/vereinheitlichen (Fallback: schlanker Slug)."""
    s = subject.strip().lower()
    mapping = {
        "deutsch": "de",
        "mathematik": "ma",
        "englisch": "en",
        "biologie": "bio",
        "chemie": "ch",
        "physik": "ph",
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
    slug = re.sub(r"[^a-z0-9]+", "", s)
    return (slug[:3] or "fach")

# ---------- NEU: robuste Schüler-Info aus <div class="pupilinfo"> ----------

def _extract_student_from_pupilinfo(soup: BeautifulSoup) -> Tuple[str, str]:
    """
    Liest Name/Klasse aus:
      <div class="pupilinfo">
        <table class="t01">
          <tr><td>Name:</td><td>…</td></tr>
          <tr><td>Klasse:</td><td>…</td></tr>
    """
    div = soup.select_one("div.pupilinfo")
    if not div:
        return "", ""
    table = div.select_one("table.t01") or div.find("table")
    if not table:
        return "", ""
    rows = _table_rows(table)
    name = ""
    klasse = ""
    for r in rows:
        if len(r) < 2:
            continue
        key = r[0].rstrip(":").strip().lower()
        val = r[1]
        if key == "name":
            name = val
        elif key == "klasse":
            klasse = val
    return name, klasse

# Fallback-Heuristik (nur falls t01 fehlt):
def _extract_student_fallback(soup: BeautifulSoup) -> Tuple[str, str]:
    info_text = " ".join(
        _norm(x.get_text())
        for x in soup.select(".panel-hint, #content h1, #content h2, #content h3")
    )
    # Name = zwei Großbuchstaben-beginnende Wörter (sehr defensiv)
    name = ""
    n = re.search(r"\b([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)+)\b", info_text)
    if n:
        name = n.group(1)
    klasse = ""
    m = re.search(r"klasse[:\s]+([A-Za-z0-9\-_/]+)", info_text, re.IGNORECASE)
    if m:
        klasse = m.group(1)
    return name, klasse

# ---------- Hauptparser ----------

def parse(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    # 1) Schüler-Info
    name, klasse = _extract_student_from_pupilinfo(soup)
    if not (name and klasse):
        # nur wenn nötig: Fallback-Heuristik
        f_name, f_klasse = _extract_student_fallback(soup)
        name = name or f_name
        klasse = klasse or f_klasse

    # 2) Noten / Hausaufgaben / Bemerkungen
    grades_by_subject: Dict[str, List[Dict[str, str]]] = {}
    homework: List[Dict[str, str]] = []
    remarks: List[Dict[str, str]] = []

    for table in soup.find_all("table"):
        rows = _table_rows(table)
        if not rows:
            continue
        head_raw = rows[0]
        head = [h.lower() for h in head_raw]

        # Noten (auch leere Tabellen registrieren!)
        if "datum" in head and "zensur" in head:
            subject = _find_subject_for_table(table) or "unbekannt"
            key = subject_key(subject)
            grades_by_subject.setdefault(key, [])
            if len(rows) > 1:
                for r in rows[1:]:
                    grades_by_subject[key].append(_row_to_entry(head_raw, r))
            continue

        # Hausaufgaben
        if {"datum", "fach", "hausaufgaben"}.issubset(set(head)):
            for r in rows[1:]:
                homework.append(_row_to_entry(head_raw, r))
            continue

        # Bemerkungen (bei dir: enthält zusätzlich „Fach“ – egal, wir prüfen als subset)
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
