"""Postal address formatting and map links."""

from urllib.parse import quote_plus


def format_address(obj) -> str:
    """'Rua X, 100 - Apto 1 - Centro, Sample/SP, 00000-000' from any object
    with the address fields (missing parts are skipped)."""
    street = " ".join(part for part in ((obj.street or "").strip(), (obj.number or "").strip()) if part)
    if obj.street and obj.number:
        street = f"{obj.street.strip()}, {obj.number.strip()}"
    line = " - ".join(part for part in (street, (obj.complement or "").strip(), (obj.district or "").strip()) if part)
    city = "/".join(part for part in ((obj.city or "").strip(), (obj.state or "").strip()) if part)
    parts = [part for part in (line, city, (obj.zip_code or "").strip()) if part]
    return ", ".join(parts)


def has_coordinates(obj) -> bool:
    return getattr(obj, "latitude", None) is not None and getattr(obj, "longitude", None) is not None


def maps_url(obj) -> str:
    """Google Maps link by coordinates when known, otherwise by address text."""
    if has_coordinates(obj):
        return f"https://www.google.com/maps/search/?api=1&query={obj.latitude},{obj.longitude}"
    address = format_address(obj)
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(address)}" if address else ""


def osm_url(obj) -> str:
    if not has_coordinates(obj):
        return ""
    return f"https://www.openstreetmap.org/?mlat={obj.latitude}&mlon={obj.longitude}#map=17/{obj.latitude}/{obj.longitude}"
