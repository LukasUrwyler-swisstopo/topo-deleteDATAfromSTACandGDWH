"""
stac_api.py  –  STAC API Hilfsfunktionen für ch.swisstopo.spezialbefliegungen

Importiert von 0_GUI_stac_gdwh_delete_Data.py.
Direkt nutzbar: python stac_api.py  (gibt Kurzinfo aus)
"""

import re
import requests
from urllib.parse import urljoin
from typing import Dict, List, Optional, Tuple


# Firmenproxy für externe Verbindungen (data.geo.admin.ch / sys-data.int.bgdi.ch)
_PROXY = {
    "http":  "http://proxy-bvcol.admin.ch:8080",
    "https": "http://proxy-bvcol.admin.ch:8080",
}

# INT/PROD sind öffentliche Endpunkte (sys-data.int.bgdi.ch / data.geo.admin.ch)
# mit regulär vertrauenswürdigen Zertifikaten – anders als GDWH (interne CA)
# gibt es hier keinen Grund, die TLS-Prüfung zu deaktivieren. DELETE-Requests
# tragen HTTP-Basic-Auth-Credentials; ohne Zertifikatsprüfung wären diese
# gegenüber einem Man-in-the-Middle im Netzwerkpfad ungeschützt.
STAC_SSL_VERIFY: bool = True

COLLECTION_ID = "ch.swisstopo.spezialbefliegungen"

# CloudFront blockiert HEAD/GET (ohne Range) für Objekte > 50 GB mit Status 400
# (siehe swisstopo-Anleitung "Downloading Large Assets (> 50 GB)"). Ein 400 auf
# HEAD ist für solche Assets also korrekt und kein echter Fehler – siehe
# _probe_large_asset/check_asset_info.
LARGE_ASSET_THRESHOLD_BYTES = 50 * 1024 ** 3

ENVIRONMENTS = {
    "INT":  "https://sys-data.int.bgdi.ch/api/stac/v0.9/",
    "PROD": "https://data.geo.admin.ch/api/stac/v0.9/",
}

# Hash-Routing-Basis des STAC-Browsers je Umgebung (für Kunden-Weitergabe).
# INT läuft direkt auf der Domain-Root, PROD unter /browser/index.html.
_BROWSER_BASE = {
    "INT":  "https://sys-data.int.bgdi.ch/#/collections/{cid}",
    "PROD": "https://data.geo.admin.ch/browser/index.html#/collections/{cid}",
}


def browser_url(env: str, item_id: Optional[str] = None) -> str:
    """Liefert den STAC-Browser-Link zur Collection, optional zu einem Item."""
    url = _BROWSER_BASE[env].format(cid=COLLECTION_ID)
    if item_id:
        url += f"/items/{item_id}"
    return url + "?.language=en"


AUFTRAGSTYPEN: Dict[str, str] = {
    "KRY (Kryosphäre)":   "kry",
    "RAM (Rapidmapping)": "ram",
    "Alle":               "",
}

EXT_PRESETS: List[Tuple[str, List[str]]] = [
    ("tif / tiff",      [".tif", ".tiff"]),
    ("copc.laz / laz",  [".copc.laz", ".laz"]),
    ("jpg / jpeg",      [".jpg", ".jpeg"]),
    ("png",             [".png"]),
    ("json",            [".json"]),
]


# ─── Interne Session-Funktionen ───────────────────────────────────────────────

# None = noch nicht getestet, True/False = Ergebnis des ersten Verbindungsversuchs.
# Ausserhalb des Bundesnetz (z.B. privater Rechner) ist proxy-bvcol.admin.ch nicht
# auflösbar -> nach einmaligem ProxyError auf Direktverbindung umschalten.
_USE_PROXY: Optional[bool] = None


def _request(method, url: str, **kwargs) -> requests.Response:
    global _USE_PROXY
    if _USE_PROXY is False:
        return method(url, proxies=None, **kwargs)
    try:
        r = method(url, proxies=_PROXY, **kwargs)
        _USE_PROXY = True
        return r
    except requests.exceptions.ProxyError:
        _USE_PROXY = False
        return method(url, proxies=None, **kwargs)


def _session_get(url: str, auth: Tuple, params: dict = None) -> requests.Response:
    return _request(requests.get, url, auth=auth, params=params,
                    verify=STAC_SSL_VERIFY, timeout=(30, 60))


def _session_delete(url: str, auth: Tuple) -> requests.Response:
    return _request(requests.delete, url, auth=auth,
                    verify=STAC_SSL_VERIFY, timeout=(30, 60))


def _session_post(url: str, auth: Tuple, json_body: dict = None) -> requests.Response:
    return _request(requests.post, url, auth=auth, json=json_body,
                    verify=STAC_SSL_VERIFY, timeout=(30, 60))


# ─── Öffentliche API-Funktionen ───────────────────────────────────────────────

def get_item_direct(base_url: str, auth: Tuple, item_id: str) -> Optional[Dict]:
    """Holt ein einzelnes Item per exakter ID. Gibt None bei 404 zurück."""
    url = urljoin(base_url, f"collections/{COLLECTION_ID}/items/{item_id.strip()}")
    r = _session_get(url, auth)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def get_collection_items(base_url: str, auth: Tuple, log_fn=print) -> List[Dict]:
    """Holt alle Items der Collection mit Paginierung."""
    all_items = []
    url    = urljoin(base_url, f"collections/{COLLECTION_ID}/items")
    params = {"limit": 1000}
    while url:
        r = _session_get(url, auth, params)
        r.raise_for_status()
        data = r.json()
        all_items.extend(data.get("features", []))
        nxt = next((lk for lk in data.get("links", []) if lk.get("rel") == "next"), None)
        if nxt:
            url    = nxt["href"]
            params = None
            log_fn(f"  Paginierung … bisher {len(all_items)} Items geladen\n")
        else:
            url = None
    return all_items


def _error_reason(r: requests.Response) -> str:
    """Extrahiert eine möglichst aussagekräftige Fehlermeldung aus einer
    fehlgeschlagenen Response – STAC/OGC-API-Fehler kommen meist als JSON
    ({"code": ..., "description": ...} o.ä.), sonst Klartext/Statustext."""
    try:
        data = r.json()
        if isinstance(data, dict):
            msg = data.get("description") or data.get("detail") or data.get("message")
            if msg:
                # "description" kommt bei Validierungsfehlern oft als Liste
                # von Einzelmeldungen zurück (z.B. ["Asset X has still an
                # upload in progress"]) statt als einzelner String.
                if isinstance(msg, list):
                    return "; ".join(str(m) for m in msg)
                return str(msg)
    except ValueError:
        pass
    text = (r.text or "").strip()
    if text:
        return text[:300]
    return r.reason or ""


def delete_asset(base_url: str, auth: Tuple,
                 item_id: str, asset_key: str) -> Tuple[bool, int, str]:
    """Löscht einen einzelnen Asset.
    Gibt (Erfolg, HTTP-Statuscode, Fehlermeldung bei Misserfolg) zurück."""
    url = urljoin(base_url,
                  f"collections/{COLLECTION_ID}/items/{item_id}/assets/{asset_key}")
    r = _session_delete(url, auth)
    ok = r.status_code in (200, 204)
    return ok, r.status_code, ("" if ok else _error_reason(r))


def delete_item(base_url: str, auth: Tuple, item_id: str) -> Tuple[bool, int, str]:
    """Löscht ein Item vollständig (nur wenn leer).
    Gibt (Erfolg, HTTP-Statuscode, Fehlermeldung bei Misserfolg) zurück."""
    url = urljoin(base_url, f"collections/{COLLECTION_ID}/items/{item_id}")
    r = _session_delete(url, auth)
    ok = r.status_code in (200, 204)
    return ok, r.status_code, ("" if ok else _error_reason(r))


def list_asset_uploads(base_url: str, auth: Tuple, item_id: str, asset_key: str,
                       status: Optional[str] = "in-progress") -> Tuple[bool, int, object]:
    """Listet Multipart-Uploads eines Assets (STAC Upload-Extension, rein
    lesend) – dient dazu, einen "has still an upload in progress"-Fehler beim
    Löschen zu beheben. Endpoint/Antwortform verifiziert anhand des produktiven
    Upload-Skripts topo-rapidmapping/main_multipart_upload_via_api.py
    (_abort_previous_upload): Response ist {"uploads": [{"upload_id": ..., ...}]}.
    status=None listet alle, sonst serverseitig gefiltert.
    Gibt (Erfolg, HTTP-Code, Liste der Upload-Dicts bzw. Fehlertext) zurück."""
    url = urljoin(
        base_url,
        f"collections/{COLLECTION_ID}/items/{item_id}/assets/{asset_key}/uploads")
    params = {"status": status} if status else None
    r = _session_get(url, auth, params=params)
    if r.status_code == 200:
        try:
            data = r.json()
        except ValueError:
            return True, r.status_code, []
        uploads = data.get("uploads", []) if isinstance(data, dict) else data
        return True, r.status_code, uploads
    return False, r.status_code, _error_reason(r)


def abort_asset_upload(base_url: str, auth: Tuple, item_id: str,
                       asset_key: str, upload_id: str) -> Tuple[bool, int, str]:
    """Bricht einen offenen Multipart-Upload eines Assets ab (STAC Upload-
    Extension), damit das Asset anschliessend gelöscht werden kann.
    Erfolg erfordert HTTP 200 UND status=="aborted" im Body (siehe Referenz-
    skript oben). Gibt (Erfolg, HTTP-Statuscode, Fehlermeldung bei Misserfolg) zurück."""
    url = urljoin(
        base_url,
        f"collections/{COLLECTION_ID}/items/{item_id}/assets/{asset_key}"
        f"/uploads/{upload_id}/abort")
    r = _session_post(url, auth)
    ok = False
    if r.status_code == 200:
        try:
            ok = r.json().get("status") == "aborted"
        except ValueError:
            ok = True
    return ok, r.status_code, ("" if ok else _error_reason(r))


# Header-Namen, die auf einen CDN-/Cache-Layer vor dem eigentlichen Storage
# hindeuten (case-insensitive, requests.headers ist ein CaseInsensitiveDict).
# Dient der Diagnose von "Asset ist laut STAC gelöscht, aber via href
# weiterhin herunterladbar" – zeigt ob eine gecachte Kopie ausgeliefert wird
# (Age/X-Cache/CF-Cache-Status > 0 bzw. HIT) oder der Origin selbst antwortet.
_CACHE_DIAG_HEADERS = (
    "Cache-Control", "Age", "ETag", "Via", "Server",
    "X-Cache", "X-Cache-Status", "CF-Cache-Status",
    "X-Amz-Cf-Id", "X-Amz-Cf-Pop", "X-Served-By",
)


def _probe_large_asset(href: str, auth: Tuple) -> Optional[int]:
    """Prüft per GET Range: bytes=0-0, ob ein Asset > 50 GB ist (CloudFront
    blockiert HEAD/GET für solche Objekte mit Status 400, siehe swisstopo-
    Anleitung 'Downloading Large Assets (> 50 GB)'). Der Range-Request geht
    direkt an den S3-Origin und liefert bei Erfolg 206 mit Content-Range.
    Gibt die Gesamtgrösse in Bytes zurück, wenn das Asset tatsächlich > 50 GB
    ist, sonst None (dann war der 400er ein echter Fehler)."""
    headers = {"Range": "bytes=0-0"}
    try:
        r = _request(requests.get, href, headers=headers, verify=STAC_SSL_VERIFY,
                    timeout=(5, 15), allow_redirects=True, stream=True)
        if r.status_code in (401, 403):
            r = _request(requests.get, href, headers=headers, auth=auth,
                        verify=STAC_SSL_VERIFY, timeout=(5, 15),
                        allow_redirects=True, stream=True)
        r.close()
        if r.status_code != 206:
            return None
        m = re.search(r"/(\d+)\s*$", r.headers.get("Content-Range", ""))
        if not m:
            return None
        total_size = int(m.group(1))
        return total_size if total_size > LARGE_ASSET_THRESHOLD_BYTES else None
    except Exception:
        return None


def check_asset_info(href: str, auth: Tuple) -> Dict:
    """HEAD-Request auf Asset-URL.
    Gibt dict mit status, size_bytes, last_modified und cache_headers zurück.
    status: HTTP-Code oder negativ (-1=kein href, -2=timeout, -3=exception,
    -4=Asset > 50 GB, von CloudFront korrekterweise mit 400 auf HEAD
    beantwortet – siehe _probe_large_asset).
    cache_headers: nur die in _CACHE_DIAG_HEADERS tatsächlich vorhandenen
    Response-Header (leer, falls keiner davon gesetzt ist)."""
    result: Dict = {"status": -1, "size_bytes": None, "last_modified": None,
                    "cache_headers": {}}
    if not href:
        return result
    try:
        r = _request(requests.head, href, verify=STAC_SSL_VERIFY,
                    timeout=(5, 15), allow_redirects=True)
        if r.status_code in (401, 403):
            r = _request(requests.head, href, auth=auth, verify=STAC_SSL_VERIFY,
                        timeout=(5, 15), allow_redirects=True)
        result["status"] = r.status_code
        cl = r.headers.get("Content-Length", "")
        if cl.isdigit():
            result["size_bytes"] = int(cl)
        lm = r.headers.get("Last-Modified")
        if lm:
            result["last_modified"] = lm
        result["cache_headers"] = {
            h: r.headers[h] for h in _CACHE_DIAG_HEADERS if h in r.headers
        }

        if r.status_code == 400:
            total_size = _probe_large_asset(href, auth)
            if total_size is not None:
                result["status"]     = -4
                result["size_bytes"] = total_size
    except requests.exceptions.Timeout:
        result["status"] = -2
    except Exception:
        result["status"] = -3
    return result


def stac_item_acq_date(item: Dict) -> str:
    """Gibt das Aufnahmedatum aus der Item-ID zurück (Format: YYYY-MM-DD).
    Priorität: Item-ID (enthält immer das Befliegungsdatum), dann properties.datetime."""
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", item.get("id", ""))
    if m:
        return m.group(1)
    return item.get("properties", {}).get("datetime", "")


def stac_item_year(item: Dict) -> str:
    """Extrahiert das Aufnahmejahr aus der Item-ID (Befliegungsdatum, nicht Importdatum).
    Nutzt (?<!\\d)/(?!\\d) statt \\b: '_' zählt als Wortzeichen, ein \\b-Anker würde
    Jahre nach einem Unterstrich (z.B. '..._2023-08-15') fälschlich NICHT matchen."""
    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", item.get("id", ""))
    if m:
        return m.group(1)
    # Fallback: properties.datetime (kann Importdatum sein – nur wenn ID kein Jahr hat)
    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", item.get("properties", {}).get("datetime", ""))
    return m.group(1) if m else ""


def stac_item_area(item: Dict) -> str:
    """Gibt den AOI-Namen zurück: zuerst aus Item-Properties (falls vorhanden),
    sonst aus der Description des ersten passenden Assets."""
    props = item.get("properties", {})
    for key in ("area", "aoi", "area_name", "region"):
        val = str(props.get(key, "")).strip()
        if val:
            return val.upper()
    for asset in item.get("assets", {}).values():
        area = asset_area(asset)
        if area:
            return area.upper()
    return ""


# Bekannte Schlüssel im "Key: Value, Key: Value, ..."-Format der Asset-Description
# (z.B. "Area: RANDA, TerrainModel: ..., Acquisition time: t1,t2,t3, LineId: ...").
# Einzelne Werte (Acquisition time, LineId) enthalten selbst Kommas – ein Split
# ausschliesslich anhand dieser bekannten Schlüssel verhindert falsches Zerteilen.
_ASSET_DESC_KEYS = [
    "Area", "TerrainModel", "SourceReferenceSystem", "CameraSystem",
    "Acquisition time", "LineId", "Commentary",
]


def parse_asset_description(description: str) -> Dict[str, str]:
    """Zerlegt die Asset-Description in ein Dict der bekannten Schlüssel."""
    if not description:
        return {}
    pattern = r"(" + "|".join(re.escape(k) for k in _ASSET_DESC_KEYS) + r"):\s*"
    matches = list(re.finditer(pattern, description))
    result: Dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(description)
        result[m.group(1)] = description[start:end].rstrip(", ").strip()
    return result


def asset_area(asset: Dict) -> str:
    """Extrahiert den 'Area'-Wert aus der Asset-Description, falls vorhanden."""
    return parse_asset_description(asset.get("description", "")).get("Area", "")


if __name__ == "__main__":
    print("stac_api.py – STAC API Modul")
    print(f"  Collection:  {COLLECTION_ID}")
    print(f"  Umgebungen:  {list(ENVIRONMENTS.keys())}")
    print(f"  Endpunkte:   DELETE asset, DELETE item, GET items")
