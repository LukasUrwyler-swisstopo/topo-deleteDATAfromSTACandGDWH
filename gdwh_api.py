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
from typing import Dict, List, Optional
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


_GDWH_PROXY = {
    "http":  "http://proxy-bvcol.admin.ch:8080",
    "https": "http://proxy-bvcol.admin.ch:8080",
}


def _gdwh_session() -> requests.Session:
    """Session mit frischer SSPI-Auth und explizitem Firmen-Proxy.
    Frische HttpNegotiateAuth-Instanz verhindert State-Carryover zwischen INT und PROD.
    requests.Session() hält die TCP-Verbindung für den mehrstufigen SSPI-Handshake."""
    s = requests.Session()
    s.auth    = HttpNegotiateAuth()
    s.verify  = GDWH_SSL_VERIFY
    s.proxies = _GDWH_PROXY
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


def gdwh_import_id(imp: Dict) -> str:
    """Extrahiert die DataPackage-ID (UUID) aus einem Import-Objekt."""
    for key in ("uuid", "id", "datapackageId", "package_id", "importId"):
        if imp.get(key):
            return str(imp[key])
    return "?"


def gdwh_import_name(imp: Dict) -> str:
    """Lesbarer Anzeigename für ein DataPackage."""
    for key in ("name", "datapackageName", "package_name", "description", "label"):
        if imp.get(key):
            return str(imp[key])
    # Kein Namensfeld vorhanden: UUID gekürzt anzeigen
    uid = gdwh_import_id(imp)
    return uid[:8] + "…" if len(uid) > 8 else uid


def gdwh_import_date(imp: Dict) -> str:
    """Extrahiert und kürzt das Datum eines Imports."""
    for key in ("importDate", "date", "created_at", "createdAt", "timestamp", "created"):
        val = imp.get(key)
        if val:
            return str(val)[:16].replace("T", " ")
    return "–"


def gdwh_import_status(imp: Dict) -> str:
    """Status eines Imports."""
    for key in ("status", "state", "importStatus"):
        if imp.get(key):
            return str(imp[key])
    return ""


# ─── Bucket-Scan & XML-Parsing ───────────────────────────────────────────────

_BUCKET_BASE = r"\\v0t0020a.adr.admin.ch\iprod\gdwh-ingest"

_GDS_BUCKET_TYPE = {
    "SB_DOP":             "RASTER",
    "SB_DOP_16":          "RASTER",
    "SB_DSM":             "RASTER",
    "SB_DSM_PUNKTWOLKE":  "VECTOR",
}

# XML-Feldnamen für Metadaten-Extraktion (Vergleich erfolgt lowercase)
_XML_AREA_TAGS        = ("area",)
_XML_ITEMNAME_TAGS    = ("stacitemname", "stac_item_name", "stacitemname", "itemname")
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


def _collect_xml_files(folder_path: str, max_depth: int = 2) -> List[str]:
    """Sammelt alle XML-Dateien bis max_depth Ebenen tief."""
    found = []

    def _scan(path, depth):
        if depth < 0:
            return
        try:
            for entry in os.scandir(path):
                if entry.is_file() and entry.name.lower().endswith(".xml"):
                    found.append(entry.path)
                elif entry.is_dir():
                    _scan(entry.path, depth - 1)
        except OSError:
            pass

    _scan(folder_path, max_depth)
    return found


def _read_folder_xml(folder_path: str, log_fn=None) -> Dict:
    """Liest XML-Dateien im Ordner (bis 2 Ebenen tief) und extrahiert Metadaten."""
    result = {
        "area": "", "stacitemname": "", "line_id": "", "commentary": "",
        "auftragstyp": "", "stac_datetime": "",
    }
    candidates = _collect_xml_files(folder_path, max_depth=2)
    if log_fn and candidates:
        log_fn(f"      XML-Dateien: {[os.path.basename(p) for p in candidates]}\n")
    for xml_path in candidates:
        try:
            root = ET.parse(xml_path).getroot()
            result["area"]          = _find_xml_value(root, _XML_AREA_TAGS)
            result["stacitemname"]  = _find_xml_value(root, _XML_ITEMNAME_TAGS)
            result["line_id"]       = _find_xml_value(root, _XML_LINEID_TAGS)
            result["commentary"]    = _find_xml_value(root, _XML_COMMENTARY_TAGS)
            result["auftragstyp"]   = _find_xml_value(root, _XML_AUFTRAGSTYP_TAGS)
            result["stac_datetime"] = _find_xml_value(root, _XML_DATETIME_TAGS)
            if log_fn:
                log_fn(f"      → area={result['area']!r}  auftragstyp={result['auftragstyp']!r}"
                       f"  stac_datetime={result['stac_datetime']!r}\n")
            if any(result.values()):
                break
        except Exception as e:
            if log_fn:
                log_fn(f"      XML-Fehler {os.path.basename(xml_path)}: {e}\n")
            continue
    return result


def gdwh_scan_bucket(env: str, gds_key: str, log_fn=None) -> List[Dict]:
    """
    Scannt den GDWH-Bucket-Ordner und liefert für jeden Datenpaket-Unterordner
    die XML-Metadaten und den Änderungszeitpunkt zurück.

    Returns:
        Liste von Dicts: folder, area, stacitemname, line_id, commentary, mtime (datetime UTC)
    """
    root_path = gdwh_bucket_path(env, gds_key)
    entries = []
    if not os.path.exists(root_path):
        if log_fn:
            log_fn(f"  [Bucket] Pfad nicht erreichbar: {root_path}\n")
        return entries
    try:
        for entry in os.scandir(root_path):
            if not entry.is_dir():
                continue
            mtime = None
            try:
                mtime = datetime.fromtimestamp(
                    entry.stat().st_mtime, tz=timezone.utc)
            except OSError:
                pass
            if log_fn:
                log_fn(f"  [Bucket] {entry.name}\n")
            meta = _read_folder_xml(entry.path, log_fn=log_fn)

            # Jahr: zuerst aus Ordnername, Fallback aus stac_datetime
            year = _extract_year_from_folder(entry.name)
            if not year and meta["stac_datetime"]:
                m = re.search(r"(\d{4})", meta["stac_datetime"])
                year = m.group(1) if m else ""

            # AREA: aus XML, Fallback aus Ordnername
            area = meta["area"] or _area_from_folder_name(entry.name)

            entries.append({
                "folder":        entry.name,
                "area":          area,
                "stacitemname":  meta["stacitemname"],
                "line_id":       meta["line_id"],
                "commentary":    meta["commentary"],
                "auftragstyp":   meta["auftragstyp"],
                "stac_datetime": meta["stac_datetime"],
                "year":          year,
                "mtime":         mtime,
            })
    except (OSError, PermissionError) as e:
        if log_fn:
            log_fn(f"  [Bucket] Zugriffsfehler: {e}\n")
        return entries
    return sorted(
        entries,
        key=lambda x: x["mtime"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def gdwh_match_folder(imp: Dict, bucket_entries: List[Dict],
                       max_diff_hours: float = 12.0) -> Optional[Dict]:
    """
    Ordnet einem GDWH-Import den zeitlich nächstliegenden Bucket-Ordner zu.
    Gibt None zurück wenn der beste Match mehr als max_diff_hours entfernt liegt.
    """
    import_dt = _parse_iso_dt(imp.get("importDate", ""))
    if import_dt is None or not bucket_entries:
        return None
    best, best_secs = None, float("inf")
    for entry in bucket_entries:
        if entry["mtime"] is None:
            continue
        diff = abs((import_dt - entry["mtime"]).total_seconds())
        if diff < best_secs:
            best_secs = diff
            best = entry
    return best if best_secs <= max_diff_hours * 3600 else None


# ─── AOI-Schätztabelle (Zentroide in LV95, EPSG:2056) ────────────────────────
# Approximate centroids for known Spezialbefliegungen AOIs.
# Ergänzen wenn neue AOIs hinzukommen.
_AOI_CENTROIDS: Dict[str, tuple] = {
    "A_NEUVE":                  (2567500, 1085500),
    "AERLENGLETSCHER":          (2651000, 1175000),
    "ALETSCH_MOOSFLUE":         (2644000, 1140000),
    "BIS_HOHLICHT_TURTMANN":    (2614000, 1115000),
    "CENGALO":                  (2771000, 1122000),
    "CORVATSCH":                (2776000, 1146000),
    "DIABLONS":                 (2611000, 1111000),
    "FEE_OST":                  (2636000, 1105000),
    "FINDEL":                   (2619000, 1098000),
    "FINSTERAAR":               (2647000, 1158000),
    "GORNER":                   (2621000, 1094000),
    "GRAECHEN":                 (2634000, 1111000),
    "GRIES":                    (2666000, 1140000),
    "GROSSER_ALETSCH_SUED":     (2638000, 1135000),
    "GRUEEBU_SAAS":             (2637000, 1107000),
    "HOHBERG":                  (2683000, 1168000),
    "JEGIHORN":                 (2632000, 1106000),
    "LAUTERAAR":                (2656000, 1164000),
    "LONA":                     (2613000, 1127000),
    "MONT_ETOILE":              (2609000, 1125000),
    "MONTE_PROSA":              (2686000, 1154000),
    "OBERAAR":                  (2657000, 1160000),
    "OBERER_GRINDELWALD":       (2654000, 1172000),
    "PERROC":                   (2598000, 1105000),
    "PINCABELLA_&_LARGARIO":    (2715000, 1118000),
    "PLAINE_MORTE":             (2611000, 1139000),
    "RANDA":                    (2626000, 1105000),
    "RHONE":                    (2671000, 1155000),
    "RIENZENSTOCK":             (2713000, 1198000),
    "SCHAFBERG_MURAGL":         (2787000, 1154000),
    "SILVRETTA":                (2791000, 1191000),
    "SUVRETTA":                 (2779000, 1155000),
    "TRIFT":                    (2665000, 1177000),
    "UNTERAAR":                 (2653000, 1162000),
    "UNTERER_GRINDELWALD":      (2648000, 1170000),
    "WEISSMIES":                (2641000, 1111000),
}


def gdwh_estimate_area(imp: Dict) -> str:
    """Schätzt die AREA anhand des Footprint-Zentroiden (nächster AOI in LV95).
    Gibt einen String der Form 'OBERAAR (geschätzt)' zurück, oder '' wenn kein Footprint."""
    wkt = imp.get("footprint", "")
    if not wkt:
        return ""
    try:
        coords = re.findall(r"([\d.]+)\s+([\d.]+)", wkt)
        if not coords:
            return ""
        cx = sum(float(x) for x, _ in coords) / len(coords)
        cy = sum(float(y) for _, y in coords) / len(coords)
        best_name, best_dist = "", float("inf")
        for name, (ax, ay) in _AOI_CENTROIDS.items():
            dist = (cx - ax) ** 2 + (cy - ay) ** 2
            if dist < best_dist:
                best_dist = dist
                best_name = name
        return f"{best_name} (geschätzt)" if best_name else ""
    except Exception:
        return ""


def gdwh_import_footprint_bbox(imp: Dict) -> str:
    """Zentroid des Footprints in LV95 CH1903+ (EPSG:2056), Schweizer Notation."""
    wkt = imp.get("footprint", "")
    if not wkt:
        return ""
    try:
        coords = re.findall(r"([\d.]+)\s+([\d.]+)", wkt)
        if not coords:
            return ""
        cx = sum(float(x) for x, _ in coords) / len(coords)
        cy = sum(float(y) for _, y in coords) / len(coords)
        return f"LV95  E {_lv95(cx)} / N {_lv95(cy)}"
    except Exception:
        return ""


if __name__ == "__main__":
    print("gdwh_api.py – GDWH API Modul")
    print(f"  Umgebungen: {list(GDWH_ENVIRONMENTS.keys())}")
    print(f"  Endpunkte:  GET imports, DELETE import")
    print(f"  Auth:       Windows SSPI (aktueller Windows-User)")
