"""
gdwh_api.py  –  GDWH API Hilfsfunktionen

Authentifizierung: Windows SSPI (HttpNegotiateAuth) – kein Benutzername/Passwort nötig.
Der aktuell eingeloggte Windows-User wird automatisch verwendet (gleich wie Browser).

Endpunkte:
  GET    /api/geodatasets/{gdsKey}/data/imports           → DataPackages laden
  DELETE /api/geodatasets/{gdsKey}/data/imports/{id}      → DataPackage löschen

Swagger (INT): https://ltgdwhi.adr.admin.ch/gdwh-api/v2/swagger/index.html
"""

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import requests
import urllib3


_FALLBACK_PROXY = "http://proxy-bvcol.admin.ch:8080"


def _pip_install(pkg: str) -> bool:
    """Installiert ein Paket via pip. Versucht zuerst Proxies aus proxy_config.json,
    dann den Firmen-Fallback-Proxy, zuletzt ohne Proxy. Gibt True bei Erfolg zurück."""
    config_path = os.path.join(os.path.dirname(__file__), "secrets", "proxy_config.json")
    proxies = []
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            proxies = [p["url"] for p in cfg.get("proxies", []) if p.get("enabled") and p.get("url")]
        except Exception:
            pass
    if _FALLBACK_PROXY not in proxies:
        proxies.append(_FALLBACK_PROXY)

    trusted = ["--trusted-host", "pypi.org", "--trusted-host", "files.pythonhosted.org"]
    attempts = []
    for proxy in proxies:
        attempts.append([sys.executable, "-m", "pip", "install", "--user", pkg,
                         "--proxy", proxy] + trusted)
    attempts.append([sys.executable, "-m", "pip", "install", "--user", pkg] + trusted)

    for cmd in attempts:
        try:
            subprocess.check_call(cmd)
            return True
        except subprocess.CalledProcessError:
            continue
    return False


try:
    from requests_negotiate_sspi import HttpNegotiateAuth
except ImportError:
    print("Installiere requests-negotiate-sspi ...")
    if not _pip_install("requests-negotiate-sspi"):
        raise RuntimeError(
            "Installation von requests-negotiate-sspi fehlgeschlagen.\n"
            "Bitte manuell installieren:\n"
            f"  python -m pip install --user requests-negotiate-sspi "
            f"--proxy http://proxy-bvcol.admin.ch:8080"
        )
    from requests_negotiate_sspi import HttpNegotiateAuth

# Interne Firmen-CA nicht im Python-Truststore → Verifikation deaktivieren.
# Alternativ: GDWH_SSL_VERIFY = r"C:\pfad\zur\firma-ca.pem"
GDWH_SSL_VERIFY: bool = False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _gdwh_session() -> requests.Session:
    """Session mit frischer SSPI-Auth, direkte Verbindung ohne Proxy.
    GDWH ist ein internes System (ltgdwhi/ltgdwh.adr.admin.ch) – kein Internet-Proxy.
    requests.Session() hält die TCP-Verbindung für den mehrstufigen SSPI-Handshake aufrecht."""
    s = requests.Session()
    s.auth    = HttpNegotiateAuth()
    s.verify  = GDWH_SSL_VERIFY
    s.proxies = {"http": "", "https": ""}
    return s

GDWH_GDS_KEYS = [
    "SB_DOP",
    "SB_DOP_16",
    "SB_DSM",
    "SB_DSM_PUNKTWOLKE",
]

GDWH_ENVIRONMENTS = {
    "INT":  "https://ltgdwhi.adr.admin.ch/gdwh-api/v2/",
    "PROD": "https://ltgdwh.adr.admin.ch/gdwh-api/v2/",
}

def gdwh_get_imports(base_url: str, gds_key: str) -> List[Dict]:
    """Holt alle DataPackages (Imports) für einen GDS-Key."""
    url = f"{base_url}api/geodatasets/{gds_key}/data/imports"
    with _gdwh_session() as s:
        r = s.get(url, timeout=(30, 60))
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return data
    for key in ("items", "imports", "datapackages", "results", "data"):
        if key in data and isinstance(data[key], list):
            return data[key]
    return [data] if data else []


def gdwh_delete_import(base_url: str, gds_key: str,
                       datapackage_id: str, email: str = "") -> Dict:
    """
    Löscht alle Daten eines DataPackages permanent.
    WARNUNG: unwiderruflich, keine Wiederherstellung möglich.
    Gibt ein Job-Objekt zurück (Löschung läuft asynchron im GDWH).
    """
    url = f"{base_url}api/geodatasets/{gds_key}/data/imports/{datapackage_id}"
    params = {"email": email} if email else None
    with _gdwh_session() as s:
        r = s.delete(url, params=params, timeout=(30, 120))
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"status": str(r.status_code)}


def gdwh_delete_data_package(base_url: str, gds_key: str, datapackage_id: str) -> Dict:
    """
    Löscht den DataPackage-Ordner im Ingest-Bucket (Aufräumschritt, siehe
    Swagger: "typically after the DataPackage has been successfully
    imported"). Rührt NICHT die bereits importierten Daten im GDWH an –
    nur den Bucket-Ordner, damit im GDWH-Portal kein "hängendes" DataPackage
    mehr angezeigt wird und der Bucket für neue, saubere Imports frei ist.

    Die datapackageId ist identisch mit der uuid aus /data/imports (per
    Swagger GET /dataPackages/{id} verifiziert – derselbe Zwei-Schritt-Import-
    Prozess: POST /dataPackages legt sie an, POST /data/imports importiert
    dasselbe Package unter derselben ID).
    """
    url = f"{base_url}api/geodatasets/{gds_key}/dataPackages/{datapackage_id}"
    with _gdwh_session() as s:
        r = s.delete(url, timeout=(30, 60))
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"status": str(r.status_code)}


def gdwh_cleanup_data_package(base_url: str, gds_key: str,
                               datapackage_id: str) -> Optional[Dict]:
    """
    Best-effort-Aufräumen des Bucket-DataPackage nach einer GDWH-Löschung.

    Gibt None zurück, wenn kein DataPackage mit dieser ID (mehr) existiert
    (HTTP 404) – das ist der Normalfall, wenn der Bucket-Ordner bereits
    automatisch geräumt wurde, und explizit KEIN Fehler. Andere Fehler
    (z.B. Berechtigung, Netzwerk) werden weitergereicht, damit der Aufrufer
    sie vom eigentlichen (bereits erfolgreichen) Lösch-Job unterscheiden und
    separat protokollieren kann.
    """
    try:
        return gdwh_delete_data_package(base_url, gds_key, datapackage_id)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise


def gdwh_import_id(imp: Dict) -> str:
    """Extrahiert die DataPackage-ID (UUID) aus einem Import-Objekt."""
    for key in ("uuid", "id", "datapackageId", "package_id", "importId"):
        if imp.get(key):
            return str(imp[key])
    return "?"


def gdwh_import_date(imp: Dict) -> str:
    """Extrahiert und kürzt das Datum eines Imports."""
    for key in ("importDate", "date", "created_at", "createdAt", "timestamp", "created"):
        val = imp.get(key)
        if val:
            return str(val)[:16].replace("T", " ")
    return "–"


def gdwh_import_status(imp: Dict) -> str:
    """Extrahiert den Lebenszyklus-Status eines DataPackages (z.B. 'Imported').

    Wichtig für die Löschung: DELETE /imports/{id} schlägt fehl, wenn der
    Status nicht 'Imported' ist (siehe Fehlermeldung "must have the status
    'Imported' to be deleted"). GET /imports liefert DataPackages aber
    unabhängig vom Status zurück – ein bereits zur Löschung eingereichtes
    oder gelöschtes Package bleibt also in der Liste sichtbar, nur mit
    geändertem Status."""
    for key in ("status", "state", "importStatus", "dataPackageStatus"):
        val = imp.get(key)
        if val:
            return str(val)
    return "?"


# ─── XML-Parsing & Bucket-Pfad (Utilities) ───────────────────────────────────
#
# Die App reichert Imports nicht mehr über einen Bucket-Scan an (siehe
# FileMetadata-Suche weiter unten – der Ingest-Bucket wird nach erfolgreichem
# Import regelmässig geleert und ist daher als Metadatenquelle unzuverlässig).
# gdwh_bucket_path()/_extract_year_from_folder()/_area_from_folder_name()
# bleiben als eigenständige, getestete Utilities erhalten (z.B. für manuelle
# Bucket-Diagnose), werden von der GUI aktuell aber nicht mehr aufgerufen.

_BUCKET_BASE = r"\\v0t0020a.adr.admin.ch\iprod\gdwh-ingest"

_GDS_BUCKET_TYPE = {
    "SB_DOP":             "RASTER",
    "SB_DOP_16":          "RASTER",
    "SB_DSM":             "RASTER",
    "SB_DSM_PUNKTWOLKE":  "VECTOR",
}

# XML-Feldnamen für Metadaten-Extraktion (Vergleich erfolgt lowercase)
_XML_AREA_TAGS        = ("area",)
_XML_LINEID_TAGS      = ("line_id", "lineid")
_XML_COMMENTARY_TAGS  = ("commentary", "kommentar", "comment", "description")
_XML_AUFTRAGSTYP_TAGS = ("auftragstyp", "auftragstype", "ordertype", "type")
_XML_DATETIME_TAGS    = ("stacitemiddatetime", "stac_item_id_datetime",
                         "stacdatetime", "acquisitiondate", "datetime")


def gdwh_bucket_path(env: str, gds_key: str) -> str:
    """Gibt den UNC-Pfad zum GDWH-Bucket-Ordner zurück."""
    bucket = "BUCKET_INT" if env == "INT" else "BUCKET"
    btype  = _GDS_BUCKET_TYPE.get(gds_key, "RASTER")
    return os.path.join(_BUCKET_BASE, bucket, btype, gds_key)


def _parse_iso_dt(s: str) -> Optional[datetime]:
    """ISO-8601-Datum parsen, Python-3.6-kompatibel."""
    s = s.strip().rstrip("Z")
    s = re.sub(r"(\.\d{6})\d*", r"\1", s)   # Mikrosekunden auf 6 Stellen kürzen
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _find_xml_value(root: ET.Element, tags) -> str:
    """Sucht namespace-agnostisch nach dem ersten passenden Tag (alle Ebenen)."""
    tag_set = set(t.lower() for t in tags)
    for el in root.iter():
        # Namespace-Präfix entfernen: '{http://...}Tag' → 'tag'
        local = re.sub(r"^\{[^}]+\}", "", el.tag).lower()
        if local in tag_set and el.text and el.text.strip():
            return el.text.strip()
    return ""


def _lv95(val: float) -> str:
    """LV95-Koordinate im Schweizer Format (Apostroph als Tausendertrennzeichen)."""
    return f"{int(val):,}".replace(",", "'")


def _extract_year_from_folder(folder_name: str) -> str:
    """Extrahiert das Jahr aus dem Ordnernamen, z.B. '2023_OBERAAR_DSM' → '2023'."""
    m = re.match(r"(\d{4})[_\-]", folder_name)
    return m.group(1) if m else ""


def _area_from_folder_name(folder_name: str) -> str:
    """Leitet AREA aus Ordnernamen ab, z.B. '2023_OBERAAR_DSM' → 'OBERAAR'.
    Entfernt Jahr-Präfix und bekannte Typ-Suffixe (DSM, DOP, PointCloud usw.)."""
    _TYPE_PARTS = {"DSM", "DOP", "DOP16", "DOP_16", "POINTCLOUD", "PUNKTWOLKE"}
    name = re.sub(r"^\d{4}[_\-]", "", folder_name)
    parts = re.split(r"[_\-]", name)
    area_parts = []
    for p in parts:
        if p.upper() in _TYPE_PARTS:
            break
        area_parts.append(p)
    return "_".join(area_parts)


# ─── FileMetadata-Suche (Metadatenquelle statt Bucket-Scan) ─────────────────
#
# GET /data/imports liefert nur uuid/gdsKey/importDate/footprint – keine
# fachlichen Attribute. Der bisherige Bucket-Scan (gdwh_scan_bucket) versuchte
# Area/Jahr/LineID stattdessen aus den Ingest-XML-Dateien im Bucket-Ordner zu
# lesen. Das schlägt fehl, sobald der Bucket-Ordner geleert ist (Standard-
# betrieb: Ingest-Buckets werden nach erfolgreichem Import regelmässig
# geräumt) – das Package existiert dann weiterhin im GDWH, ist aber nicht mehr
# zuordenbar.
#
# POST /fileMetadata/search liegt dauerhaft im GDWH (unabhängig vom Bucket)
# und trägt bereits alle nötigen Attribute: temporalKey (Jahr), tileKey sowie
# customAttributes – ein XML-Fragment mit denselben Feldern (Area, LineID,
# Auftragstyp, Commentary, StacItemIdDatetime), die zuvor aus den Bucket-XMLs
# gelesen wurden. Der Join zu einem Import läuft über importUuid == uuid.

def gdwh_search_file_metadata(base_url: str, gds_key: str) -> List[Dict]:
    """Holt die aktuellen FileMetadata-Einträge (mostRecent) für einen GDS-Key."""
    url = f"{base_url}api/geodatasets/{gds_key}/fileMetadata/search"
    payload = {"gdsKey": gds_key, "mostRecent": True}
    with _gdwh_session() as s:
        r = s.post(url, json=payload, timeout=(30, 60))
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def _parse_custom_attributes(xml_fragment: str) -> Dict:
    """Parst das customAttributes-XML-Fragment eines FileMetadata-Eintrags.

    Kein eigenständiges XML-Dokument (mehrere Wurzel-Tags aneinandergereiht),
    daher künstliches Wurzelelement zum Parsen nötig."""
    result = {"area": "", "line_id": "", "commentary": "", "auftragstyp": "", "stac_datetime": ""}
    if not xml_fragment:
        return result
    try:
        root = ET.fromstring(f"<root>{xml_fragment}</root>")
    except ET.ParseError:
        return result
    result["area"]          = _find_xml_value(root, _XML_AREA_TAGS)
    result["line_id"]       = _find_xml_value(root, _XML_LINEID_TAGS)
    result["commentary"]    = _find_xml_value(root, _XML_COMMENTARY_TAGS)
    result["auftragstyp"]   = _find_xml_value(root, _XML_AUFTRAGSTYP_TAGS)
    result["stac_datetime"] = _find_xml_value(root, _XML_DATETIME_TAGS)
    return result


def gdwh_index_file_metadata_by_import(file_metadata: List[Dict]) -> Dict[str, Dict]:
    """Baut ein Lookup importUuid → angereicherte Metadaten (Area, Jahr, LineID, …).

    Ein Import kann mehrere FileMetadata-Einträge haben (z.B. je Kachel) – für
    die Anzeige genügt ein repräsentativer Eintrag pro Import, der erste
    Treffer wird verwendet."""
    index: Dict[str, Dict] = {}
    for fm in file_metadata:
        import_uuid = fm.get("importUuid")
        if not import_uuid or import_uuid in index:
            continue
        attrs = _parse_custom_attributes(fm.get("customAttributes", ""))
        file_format = fm.get("fileFormat") or {}
        index[import_uuid] = {
            "area":          attrs["area"],
            "line_id":       attrs["line_id"],
            "commentary":    attrs["commentary"] or fm.get("commentary", ""),
            "auftragstyp":   attrs["auftragstyp"],
            "stac_datetime": attrs["stac_datetime"],
            "year":          str(fm.get("temporalKey") or ""),
            "tile_key":      fm.get("tileKey", ""),
            "file_format":   file_format.get("name", ""),
            "file_extension": file_format.get("extension", ""),
        }
    return index


def _polygon_centroid(coords: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Echter Flächenschwerpunkt eines Polygon-Rings via Shoelace-Formel.

    Ein einfacher Eckpunkt-Mittelwert (arithmetisches Mittel aller Koordinaten)
    ist nur bei symmetrischen Polygonen korrekt und wird bei ungleichmässiger
    Punktdichte (z.B. mehr Stützpunkten auf einer Seite, oder dem doppelt
    gezählten Schlusspunkt eines geschlossenen Rings) spürbar verzerrt.

    Fällt bei entarteten Ringen (Fläche ≈ 0, z.B. Linie/Punkt oder < 3 Punkte)
    auf den einfachen Mittelwert zurück.
    """
    n = len(coords)
    if n < 3:
        return (sum(x for x, _ in coords) / n, sum(y for _, y in coords) / n)
    area2, cx, cy = 0.0, 0.0, 0.0
    for i in range(n):
        x0, y0 = coords[i]
        x1, y1 = coords[(i + 1) % n]
        cross  = x0 * y1 - x1 * y0
        area2 += cross
        cx    += (x0 + x1) * cross
        cy    += (y0 + y1) * cross
    area = area2 / 2.0
    if abs(area) < 1e-9:
        return (sum(x for x, _ in coords) / n, sum(y for _, y in coords) / n)
    return (cx / (6.0 * area), cy / (6.0 * area))


def gdwh_import_footprint_bbox(imp: Dict) -> str:
    """Echter Flächenschwerpunkt des Footprints in LV95 CH1903+ (EPSG:2056),
    Schweizer Notation. Geht wie bisher von einem einzelnen (äusseren)
    Polygon-Ring aus – Multi-Polygone/Löcher werden nicht gesondert behandelt."""
    wkt = imp.get("footprint", "")
    if not wkt:
        return ""
    try:
        coords = [(float(x), float(y))
                  for x, y in re.findall(r"([\d.]+)\s+([\d.]+)", wkt)]
        if not coords:
            return ""
        cx, cy = _polygon_centroid(coords)
        return f"LV95  E {_lv95(cx)} / N {_lv95(cy)}"
    except Exception:
        return ""


if __name__ == "__main__":
    print("gdwh_api.py – GDWH API Modul")
    print(f"  Umgebungen: {list(GDWH_ENVIRONMENTS.keys())}")
    print(f"  Endpunkte:  GET imports, DELETE import")
    print(f"  Auth:       Windows SSPI (aktueller Windows-User)")
