"""Place lookups for news stories: OpenStreetMap Nominatim (free, no key, 1 request/second)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy requires an identifying User-Agent.
USER_AGENT = "eye-of-horus/1.0 (news event mapping; +https://github.com/tm1944/eye-of-horus)"


def build_query(location: dict, specificity: str) -> str:
    """'City, Country', 'Region, Country' or 'Country', as precise as the specificity allows."""
    country = (location.get("country") or "").strip()
    region = (location.get("region") or "").strip()
    city = (location.get("city") or "").strip()
    if specificity in ("precise", "city") and city and country:
        return f"{city}, {country}"
    if specificity in ("precise", "city", "region") and region and country:
        return f"{region}, {country}"
    if specificity in ("precise", "city", "region", "country"):
        return country
    return ""  # global or continent: no single place


def geocode(query: str) -> dict | None:
    """{'lat': float, 'lng': float} for the first match, or None. The caller waits 1 s between calls."""
    url = f"{NOMINATIM}?{urllib.parse.urlencode({'q': query, 'format': 'json', 'limit': 1})}"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode())
        if data:
            return {"lat": float(data[0]["lat"]), "lng": float(data[0]["lon"])}
    except Exception:  # noqa: BLE001 — a failed lookup falls back to the country centre
        pass
    return None
