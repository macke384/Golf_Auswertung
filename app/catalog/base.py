"""Gemeinsame Bausteine für Platzkataloge (externe, nicht vertrauenswürdige Quellen)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

MAX_BYTES = 4 * 1024 * 1024
TIMEOUT_S = 10

# fetcher(url, headers) -> geparstes JSON; in Tests durch Beispieldaten ersetzt
Fetcher = Callable[..., Any]


class CatalogError(Exception):
    """Katalog nicht erreichbar, Kontingent erschöpft oder Antwort unbrauchbar."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


def http_get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    """GET mit Timeout und Größenlimit; liefert geparstes JSON oder CatalogError.

    Fehlermeldungen enthalten bewusst weder URL noch Header (API-Schlüssel).
    """
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "Golf_Auswertung/0.1", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 (feste Basis-URL)
            if resp.status != 200:
                raise CatalogError(f"Platzkatalog antwortet mit Status {resp.status}.")
            data = resp.read(MAX_BYTES + 1)
    except CatalogError:
        raise
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise CatalogError("Platz im Katalog nicht gefunden.", 404) from None
        if e.code == 401:
            raise CatalogError("Der API-Schlüssel für den Platzkatalog fehlt oder ist ungültig.", 502) from None
        if e.code == 403:
            raise CatalogError("Der Platzkatalog verweigert den Zugriff (Konto nicht aktiv oder Funktion "
                               "nicht im Tarif enthalten).", 502) from None
        if e.code == 429:
            raise CatalogError("Das Tageskontingent des Platzkatalogs ist aufgebraucht.", 429) from None
        raise CatalogError(f"Platzkatalog antwortet mit Status {e.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CatalogError("Platzkatalog ist nicht erreichbar.", 503) from None
    if len(data) > MAX_BYTES:
        raise CatalogError("Antwort des Platzkatalogs ist zu groß.")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CatalogError("Antwort des Platzkatalogs ist kein gültiges JSON.") from None
