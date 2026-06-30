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
import subprocess
import sys
import requests
import urllib3
from typing import Dict, List


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

_AUTH = HttpNegotiateAuth()


def gdwh_get_imports(base_url: str, gds_key: str) -> List[Dict]:
    """Holt alle DataPackages (Imports) für einen GDS-Key."""
    url = f"{base_url}api/geodatasets/{gds_key}/data/imports"
    r = requests.get(url, auth=_AUTH, timeout=(30, 60), verify=GDWH_SSL_VERIFY)
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
    r = requests.delete(url, auth=_AUTH, params=params,
                        timeout=(30, 120), verify=GDWH_SSL_VERIFY)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"status": str(r.status_code)}


def gdwh_import_id(imp: Dict) -> str:
    """Extrahiert die DataPackage-ID aus einem Import-Objekt."""
    for key in ("id", "datapackageId", "package_id", "importId"):
        if imp.get(key):
            return str(imp[key])
    return "?"


def gdwh_import_name(imp: Dict) -> str:
    """Lesbarer Anzeigename für ein DataPackage."""
    for key in ("name", "datapackageName", "package_name", "description", "label"):
        if imp.get(key):
            return str(imp[key])
    return gdwh_import_id(imp)


def gdwh_import_date(imp: Dict) -> str:
    """Extrahiert und kürzt das Datum eines Imports."""
    for key in ("date", "importDate", "created_at", "createdAt", "timestamp", "created"):
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


if __name__ == "__main__":
    print("gdwh_api.py – GDWH API Modul")
    print(f"  Umgebungen: {list(GDWH_ENVIRONMENTS.keys())}")
    print(f"  Endpunkte:  GET imports, DELETE import")
    print(f"  Auth:       Windows SSPI (aktueller Windows-User)")
