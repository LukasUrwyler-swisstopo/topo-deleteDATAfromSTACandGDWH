# STAC / GDWH Deleting-Tool

GUI-Tool zum gezielten Löschen von Daten aus:

- **Tab 1 — STAC**: Assets (und bei Bedarf leere Items) aus der Collection `ch.swisstopo.spezialbefliegungen`
- **Tab 2 — GDWH**: DataPackage-Imports aus dem Geodata-Warehouse (`ltgdwhi` / `ltgdwh`)

**Hintergrund:** Die Pipeline läuft GDWH → automatisierter STAC-Upload. Beim Re-Import müssen beide Systeme bereinigt werden.

---

## Voraussetzungen

- Python 3.6+
- Pakete: `requests`, `requests-negotiate-sspi` (tkinter ist in der Standardbibliothek enthalten)

Das Script versucht beim Start fehlende Pakete **automatisch** über den Firmenproxy zu installieren.

Falls die automatische Installation fehlschlägt, manuell ausführen:

```cmd
python -m pip install --user requests-negotiate-sspi --proxy http://proxy-bvcol.admin.ch:8080 --trusted-host pypi.org --trusted-host files.pythonhosted.org
```

---

## Ordnerstruktur

```
topo-deleteDATAfromSTAC/
├── 0_GUI_stac_gdwh_delete_Data.py    ← Einstiegspunkt (GUI, 2 Tabs)
├── stac_api.py                        ← STAC API-Funktionen (Modul)
├── gdwh_api.py                        ← GDWH API-Funktionen (Modul)
├── test_functions.py                  ← pytest-Tests (72 Tests)
├── secrets/
│   ├── stac_credentials.json          ← STAC-Zugangsdaten (nicht in Git!)
│   └── proxy_config.json              ← Proxy-Konfiguration (optional)
├── logs/                              ← Tages-Logs (nicht in Git!)
├── .gitignore
└── README.md
```

### `secrets/stac_credentials.json`

```json
{
    "INT": {
        "username": "...",
        "password": "..."
    },
    "PROD": {
        "username": "...",
        "password": "..."
    }
}
```

> `secrets/` ist über `.gitignore` vom Git-Tracking ausgeschlossen — Credentials nie committen.

---

## Starten

```bash
python 0_GUI_stac_gdwh_delete_Data.py
```

---

## Tab 1 — STAC

Löscht Assets aus `ch.swisstopo.spezialbefliegungen` via swisstopo Transactional API.  
Wird ein Item durch die Löschung **vollständig leer** (alle Assets entfernt), wird das Item anschliessend automatisch mitgelöscht.

### Schritt 1 — Umgebung & Credentials

- **INT** = Integrationsumgebung (`sys-data.int.bgdi.ch`) — zum Testen
- **PROD** = Produktionsumgebung (`data.geo.admin.ch`) — Live-Daten

`Credentials laden` liest die Zugangsdaten aus `secrets/stac_credentials.json`.  
Erst danach werden die Suchbuttons aktiviert.

---

### Schritt 2 — Auftragstyp, Item & Asset-Filter

#### Auftragstyp

| Auftragstyp | Such-Vorschlag |
|---|---|
| KRY (Kryosphäre) | `kry` |
| RAM (Rapidmapping) | `ram` |
| Alle | *(leer)* |

#### Item-ID Suche

| Button | Verhalten |
|---|---|
| **Exakt abrufen (1 Item)** | Direkter API-Call mit vollständiger Item-ID — sofort, 1 Request |
| **Alle suchen + filtern** | Lädt alle Items der Collection, filtert nach Teilstring — langsam bei 5000+ Items |

> **Teilstring-Beispiele:** `2024-08-20`, `kry-2024`, `t10270000`

#### Asset-Key Filter

Filtert Assets nach einem Teilstring im Key, z.B. `nrgb`, `16bit`, `thumbnail`.  
Leer lassen = alle Assets anzeigen.

#### Dateiendungs-Filter

Checkboxen für häufige Typen: `tif/tiff`, `copc.laz/laz`, `jpg/jpeg`, `png`, `json`.  
Zusätzlich Freitext für weitere Endungen (z.B. `gpkg pdf`).

Filteränderungen wirken **sofort** auf die geladenen Daten — kein Neu-Abruf nötig.

---

### Schritt 3 — Assets auswählen

Nach dem Laden erscheinen alle gefilterten Assets als Checkboxen:

```
▸  kry-2024-08-20t10270000
─────────────────────────────────────────────────────────
☐  kry-2024-08-20t10270000-nrgb-16bit    .tif
☐  kry-2024-08-20t10270000-thumbnail     .jpg
```

**Standardmässig sind alle Assets abgewählt** — die Auswahl muss bewusst getroffen werden.

#### Auswahlsteuerung

| Button | Funktion |
|---|---|
| Alle auswählen | Alle sichtbaren Assets ankreuzen |
| Alle abwählen | Alle abwählen |
| **Assets prüfen (HEAD)** | HTTP-HEAD-Request je Asset → Statusanzeige |
| **Fehlerhafte auswählen** | Alle Assets mit Fehler-Status automatisch ankreuzen |

#### Asset-Prüfung (HEAD-Requests)

Prüft die Erreichbarkeit der Dateien direkt auf dem Server (6 parallele Requests).

| Anzeige | Bedeutung |
|---|---|
| `⟳` | Wird gerade geprüft |
| `✓ 200` grün | Asset erreichbar und korrekt |
| `✗ 400` rot | Korrupt / Bad Request → Kandidat zum Löschen |
| `✗ 404` rot | Datei nicht vorhanden |
| `✗ timeout` orange | Netzwerk-Timeout |

---

### Schritt 4 — Löschung ausführen

Der Lösch-Button zeigt immer die aktuelle Auswahl:

```
Asset Auswahl (3) löschen
```

Vor der Löschung erscheint ein **zweistufiger Sicherheitsdialog**:
1. Checkbox bestätigen: *"Ich verstehe, dass die Assets permanent gelöscht werden"*
2. Umgebungsname eintippen (`INT` oder `PROD`)

Das Log protokolliert jeden gelöschten Asset mit Status `[OK]` oder `[FAIL]`.

**Item-Löschung:** Werden durch die Auswahl alle Assets eines Items entfernt, löscht das Tool das nun leere Item automatisch nach. Haben andere Assets im gleichen Item keine Checkbox gesetzt, bleibt das Item vollständig erhalten.

---

### Typischer Workflow STAC — Korrupte Assets bereinigen

```
1.  Umgebung wählen (INT zum Testen, PROD für Live-Daten)
2.  Credentials laden
3.  Auftragstyp wählen (KRY / RAM)
4.  Item-ID oder Datum eingeben  →  "Alle suchen + filtern"
       Beispiel: "2024-08-20"
5.  [Assets prüfen (HEAD)]
       → fehlerhafte Assets werden rot markiert (✗ 400 / ✗ 404)
6.  [Fehlerhafte auswählen]
7.  [Asset Auswahl (n) löschen]  →  Sicherheitsdialog bestätigen
8.  Korrektes Asset über den normalen Importprozess neu eintragen
```

---

## Tab 2 — GDWH

Löscht DataPackage-Imports aus dem Geodata-Warehouse via GDWH-API v2.  
Die Löschung ist **asynchron** — das GDWH startet einen Job und meldet den Abschluss optional per E-Mail.

> **Erreichbarkeit:** Die GDWH-Hosts (`ltgdwhi.adr.admin.ch` / `ltgdwh.adr.admin.ch`) sind nur im internen Netz / VPN erreichbar.

---

### Schritt 1 — Umgebung

- **INT** = Integrationsumgebung (`ltgdwhi.adr.admin.ch`)
- **PROD** = Produktionsumgebung (`ltgdwh.adr.admin.ch`)

Authentifizierung läuft automatisch über die **Windows-Session** (SSPI) — kein Benutzername/Passwort nötig, genau wie im Browser.

---

### Schritt 2 — GDS-Key eingeben & Imports laden

GDS-Key eingeben (z.B. `SB_DSM`, `SB_DOP`) und `Imports laden` klicken.

Die Liste zeigt alle vorhandenen DataPackages mit ID, Name, Datum und Status.

---

### Schritt 3 — Imports auswählen

DataPackages via Checkbox markieren.

| Button | Funktion |
|---|---|
| Alle auswählen | Alle sichtbaren Imports ankreuzen |
| Alle abwählen | Alle abwählen |

---

### Schritt 4 — Löschung ausführen

Optional: E-Mail-Adresse für Job-Abschluss-Benachrichtigung eingeben.

Der Lösch-Button zeigt die aktuelle Auswahl:

```
Import Auswahl (2) löschen
```

Vor der Löschung erscheint ein **zweistufiger Sicherheitsdialog** analog zum STAC-Tab.

Das Log protokolliert den gestarteten Lösch-Job pro Import mit Job-ID und initialem Status.

---

### Typischer Workflow GDWH — DataPackage entfernen

```
1.  Umgebung wählen (INT zum Testen, PROD für Live-Daten)
2.  AD-Credentials (Windows-Login) eingeben
3.  GDS-Key eingeben  →  [Imports laden]
4.  Zu löschende DataPackages ankreuzen
5.  Optional: E-Mail für Job-Benachrichtigung eingeben
6.  [Import Auswahl (n) löschen]  →  Sicherheitsdialog bestätigen
7.  Job-ID aus dem Log notieren — Abschluss folgt per E-Mail oder direkt im GDWH prüfen
```

---

## Tests

```bash
pytest test_functions.py -v
```

72 Tests decken alle API-Funktionen in `stac_api.py` und `gdwh_api.py` ab (HTTP-Calls werden gemockt).

---

## Hinweise

- Für Einsatz hinter einem Proxy: `PROXY_AVAILABLE = True` in `stac_api.py` setzen und `secrets/proxy_config.json` anpassen.
- `logs/` enthält Tages-Logs und ist nicht im Git-Tracking.
- STAC-Endpunkte: swisstopo Transactional API (`DELETE /collections/{id}/items/{itemId}/assets/{assetKey}`, `DELETE /collections/{id}/items/{itemId}`)
- GDWH-Endpunkte: GDWH-API v2 (`GET /api/geodatasets/{gdsKey}/data/imports`, `DELETE /api/geodatasets/{gdsKey}/data/imports/{datapackageId}`)
