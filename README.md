# STAC / GDWH Deleting-Tool

GUI-Tool zum gezielten Löschen von Daten aus:

- **Tab 1 — STAC [INT/PROD]**:<br>
  Assets (und bei Bedarf leere Items) aus der Collection `ch.swisstopo.spezialbefliegungen`
- **Tab 2 — GDWH [INT/PROD]**:<br>
  DataPackage-Imports aus dem Geodata-Warehouse (`ltgdwhi` / `ltgdwh`)

## GUI

```bash
python 0_GUI_stac_gdwh_delete_Data.py
```

<img width="958" height="1027" alt="grafik" src="https://github.com/user-attachments/assets/618f4c2e-ab3b-4285-a292-49123efd2ca8" />


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
├── test_functions.py                  ← pytest-Tests (92 Tests)
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

Reihenfolge in Schritt 1: **Umgebung (INT/PROD) → `Credentials laden` (mit Status-Text daneben) → `STAC Browser öffnen` → URL-Hinweis.**

`Credentials laden` liest die Zugangsdaten aus `secrets/stac_credentials.json` (Button ist amber, solange nicht geladen).  
Erst danach wird der `Laden`-Button aktiviert.

> Nach dem **ersten erfolgreichen Laden** wechselt der Button-Text dauerhaft von `ITEM-Liste laden` auf `ITEM-Liste aktualisieren`. Ein erneuter Klick leert die Baumansicht sofort und lädt danach neu. Bei Umgebungswechsel wird der Button-Text (und die Auswahl) zurückgesetzt.

`STAC Browser öffnen` öffnet den swisstopo STAC-Browser für die gewählte Umgebung/Collection im Standardbrowser und kopiert den Link in die Zwischenablage.

---

### Schritt 2 — Auftragstyp, Item & Asset-Filter

#### Auftragstyp

| Auftragstyp | Such-Vorschlag |
|---|---|
| KRY (Kryosphäre) | `kry` |
| RAM (Rapidmapping) | `ram` |
| Alle | *(leer)* |

#### Item-ID Suche

Ein einziger **`Laden`**-Button (unterhalb des Dateiendungs-Filters) übernimmt beide Fälle automatisch:

1. Erst wird die Eingabe als **vollständige Item-ID** direkt abgerufen (1 Request, sofort).
2. Kein Treffer (oder Feld leer) → das Tool lädt **alle Items der Collection** und filtert nach Teilstring — langsam bei 5000+ Items, bei leerem Feld folgt eine Sicherheitsabfrage.

> **Teilstring-Beispiele:** `2024-08-20`, `kry-2024`, `t10270000`

Neben `Laden` stehen **`Alle aufklappen`** / **`Alle einklappen`** zur Verfügung, um die Item/Asset-Baumansicht in Schritt 3 auf- bzw. zuzuklappen.

#### Asset-Key Filter

Filtert Assets nach einem Teilstring im Key, z.B. `nrgb`, `16bit`, `thumbnail`.  
Leer lassen = alle Assets anzeigen.

#### Dateiendungs-Filter

Checkboxen für häufige Typen: `tif/tiff`, `copc.laz/laz`, `jpg/jpeg`, `png`, `json`.  
Zusätzlich Freitext für weitere Endungen (z.B. `gpkg pdf`).

Filteränderungen wirken **sofort** auf die geladenen Daten — kein Neu-Abruf nötig.

---

### Schritt 3 — Assets auswählen

Nach dem Laden erscheinen alle gefilterten Items als **Baumansicht (Treeview)**, **sortiert nach Aufnahmedatum (neueste zuerst)**, mit den Spalten *Auswahl / Area / Status / Typ / Grösse / Geändert*:

```
Item / Asset                              Auswahl  Area     Status     Typ    Grösse    Geändert
▾ kry-2024-08-20t10270000  [OBERAAR  2024-08-20]      ◯    OBERAAR              2 Assets
      nrgb-16bit-cog.tif                              ◯    OBERAAR   ✓ 200   .tif   345.6 MB  2026-04-27
      thumbnail.jpg                                   ◯              ✓ 200   .jpg    61.2 KB  2026-06-16
▾ ram-2024-06-24t10520200  [LEER]                      ◯                     0 Assets (leer)
```

- **Area** wird zuerst aus den Item-Properties, sonst aus der Asset-Description (`Area: ...`) extrahiert.
- Der Collection-Präfix `ch.swisstopo.spezialbefliegungen_` wird im Item-Namen ausgeblendet, Aufnahmedatum/Area erscheinen im Item-Label.
- Auswahl erfolgt per Klick auf die **Kreis-Glyphen**: ◯ nicht ausgewählt · ⬤ ausgewählt (amber eingefärbt) · ◐ (nur beim Item) teilweise ausgewählt. Eine Item-Zeile wird nur dann komplett amber, wenn **alle** ihre Assets ausgewählt sind. Liegt für ein Asset bereits ein Prüfergebnis vor (grün/rot/orange, siehe unten), hat dessen Farbe Vorrang vor der Amber-Auswahlmarkierung — ausgewählte Zeilen werden zusätzlich **fett** dargestellt, damit die Auswahl auch bei einer bereits farbig eingefärbten (fehlerhaften) Zeile eindeutig sichtbar bleibt.
- **Standardmässig sind alle Assets abgewählt** — die Auswahl muss bewusst getroffen werden (anders als im read-only Monitoring-Tool).
- Rechtsklick auf eine Zeile öffnet ein Kontextmenü (URL kopieren, im Browser öffnen, Item-ID kopieren, im STAC Browser öffnen); Doppelklick auf ein Asset öffnet dessen URL direkt im Browser.

#### Leere Items (Items ohne Assets)

Items ganz ohne Assets werden ebenfalls angezeigt (rot/kursiv, `0 Assets (leer)`) statt unsichtbar zu verschwinden — inkl. Auffindbarkeit über die Item-ID-Suche. Da sie keine Asset-Kindzeilen haben, sitzt die Lösch-Checkbox direkt auf der Item-Zeile; beim Löschen wird das Item **direkt** entfernt (`DELETE .../items/{id}`), ohne vorgelagerten Asset-Löschschritt.

#### Auswahlsteuerung

| Button | Funktion |
|---|---|
| Alle auswählen | Alle sichtbaren Assets + leeren Items ankreuzen (●) |
| Alle abwählen | Alle abwählen (○) |
| **Assets prüfen (HEAD)** | HTTP-HEAD-Request je Asset → Status/Grösse/Geändert. Button-Text ist amber, solange noch nicht geprüft wurde, und wird beim ersten Klick grün. |
| **Fehlerhafte anzeigen** | Blendet die Baumansicht auf fehlerhafte Assets (Status err/warn) **und** leere Items ein/aus — kombinierbar mit den übrigen Filtern. Button-Text wechselt zu `Alle Assets wieder anzeigen`. |
| **Fehlerhafte auswählen** | Ersetzt die Auswahl durch alle Assets mit Fehler-Status **und** alle leeren Items — beide gelten als "fehlerhaft" im Sinne dieses Buttons. Auch ohne vorherige HEAD-Prüfung nutzbar, sobald leere Items geladen sind. |
| **ITEMs ohne Thumbnail** (nur bei Auftragstyp RAM) | Blendet die Baumansicht auf Items ohne `thumbnail.jpg`-Asset ein/aus (reine Metadaten-Prüfung, keine HEAD-Prüfung nötig). Items mit `t23595900` im Namen (Tagesübersicht-Items mit KML-Platzhalter, feste Zeit 23:59:59) haben planmässig nie ein Thumbnail und werden ausgeschlossen. Button-Text wechselt ebenfalls zu `Alle Assets wieder anzeigen`. |

#### Asset-Prüfung (HEAD-Requests)

Prüft die Erreichbarkeit der Dateien direkt auf dem Server (6 parallele Requests) und liest zusätzlich Dateigrösse (`Content-Length`) und Änderungsdatum (`Last-Modified`) aus den Response-Headern. Prüfergebnisse überleben einen Filterwechsel (z.B. Umschalten auf "Fehlerhafte anzeigen") und werden nicht verworfen.

| Anzeige (Status-Spalte) | Bedeutung |
|---|---|
| `⟳` | Wird gerade geprüft |
| `✓  200` grün | Asset erreichbar und korrekt |
| `✗  400` rot | Korrupt / Bad Request → Kandidat zum Löschen |
| `✗  404` rot | Datei nicht vorhanden |
| `✗  timeout` orange | Netzwerk-Timeout |

---

### Schritt 4 — Löschung ausführen

Der Lösch-Button zeigt immer die aktuelle Auswahl:

```
Asset Auswahl (3) löschen
```

Vor der Löschung erscheint ein **zweistufiger Sicherheitsdialog**:
1. Checkbox bestätigen: *"Ich verstehe, dass die Assets permanent gelöscht werden"*
2. Umgebungsname eintippen (`INT` oder `PROD`)

Das Log protokolliert jeden gelöschten Asset mit Status `[OK]` oder `[FAIL]`. Bei `[FAIL]` wird zusätzlich die **Klartext-Fehlermeldung der STAC-API** mitgeloggt (z.B. `HTTP 400 – Asset thumbnail.jpg has still an upload in progress`), nicht nur der HTTP-Code.

Nach Abschluss werden erfolgreich gelöschte Assets/Items automatisch aus der Baumansicht entfernt (kein manueller Reload nötig). Fehlgeschlagene Assets bleiben sichtbar und ausgewählt.

**Item-Löschung:** Werden durch die Auswahl alle Assets eines Items entfernt, löscht das Tool das nun leere Item automatisch nach. Haben andere Assets im gleichen Item keine Checkbox gesetzt, bleibt das Item vollständig erhalten. Bereits als **leer ausgewählte Items** (siehe oben) werden direkt gelöscht, ohne vorgelagerten Asset-Löschschritt.

**Automatische Wiederherstellung bei "upload in progress":** Scheitert eine Asset-Löschung mit der Meldung, dass noch ein Upload läuft (verwaiste Multipart-Upload-Session eines abgebrochenen Direkt-Uploads, siehe `topo-rapidmapping/main_multipart_upload_via_api.py`), versucht das Tool automatisch: offene Upload-Sessions des Assets auflisten (`GET .../assets/{key}/uploads?status=in-progress`), jede abbrechen (`POST .../uploads/{upload_id}/abort`) und die Löschung danach einmal erneut. Jeder Schritt wird im Log protokolliert.

---

### Typischer Workflow STAC — Korrupte Assets bereinigen

```
1.  Umgebung wählen (INT zum Testen, PROD für Live-Daten)
2.  Credentials laden
3.  Auftragstyp wählen (KRY / RAM)
4.  Item-ID oder Datum eingeben  →  [Laden]
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

GDS-Key eingeben (z.B. `SB_DSM`, `SB_DOP`, `SB_DSM_PUNKTWOLKE`) und `Imports laden` klicken.

> Nach dem **ersten erfolgreichen Laden** wechselt der Button-Text dauerhaft auf `Imports aktualisieren`. Ein erneuter Klick leert die Liste sofort und lädt danach neu. Bei Umgebungs- oder GDS-Key-Wechsel wird der Button-Text (und die Auswahl) zurückgesetzt.

Das Tool lädt alle DataPackages per API und reichert sie danach automatisch mit Metadaten an:

#### Datenanreicherung via FileMetadata-API

`GET /data/imports` liefert nur `uuid`/`gdsKey`/`importDate`/`footprint` – keine
fachlichen Attribute. Das Tool ruft daher zusätzlich `POST /fileMetadata/search`
für den GDS-Key ab und verknüpft jeden Import über `importUuid == uuid` mit dem
passenden FileMetadata-Eintrag. Diese Attribute liegen dauerhaft im GDWH
(unabhängig vom Ingest-Bucket, der nach erfolgreichem Import regelmässig
geleert wird) – ein früherer Bucket-Scan-Ansatz wurde deshalb ersetzt: Sobald
der Bucket-Ordner geleert war, liess sich kein Import mehr anreichern, obwohl
die Daten weiterhin im GDWH vorhanden waren.

Aus dem `customAttributes`-Feld (ein XML-Fragment), `temporalKey` und `fileFormat` werden folgende Felder extrahiert:

| Feld | Quelle | Bedeutung |
|---|---|---|
| Auftragstyp | `<Auftragstyp>` in `customAttributes` | `KRY` oder `RAM` |
| AREA | `<Area>` in `customAttributes` | AOI-Name (z.B. `OBERAAR`) |
| StacItemIdDatetime | `<StacItemIdDatetime>` in `customAttributes` | Aufnahmedatum |
| Commentary | `<Commentary>` in `customAttributes` (Fallback: `commentary`-Feld) | Freitext-Bemerkung |
| Jahr | `temporalKey` (FileMetadata) | Aufnahmejahr |
| Dateiformat | `fileFormat.name`/`.extension` (FileMetadata) | z.B. `TIFF` / `.tif` |

LineID (`<LineID>` in `customAttributes`) wird zwar mitgeladen (`match["line_id"]`), aber bewusst nicht in der Liste angezeigt.

#### Anzeige mit FileMetadata-Match

```
☐  2023  OBERAAR    [SB_DOP · TIFF]
     KRY    2023-08-15t102000
     Digital OrthoPhoto - Mosaic RGB 8BIT   ·   2023-08-20 14:39
```

#### Anzeige ohne FileMetadata-Match (Fallback)

Wenn kein passender FileMetadata-Eintrag gefunden wird (z.B. sehr alte Imports), zeigt das Tool `????` als Jahr und die DataPackage-ID statt der AREA – das Package bleibt trotzdem in der Liste und ist löschbar, wird durch den Jahresfilter also nicht ausgeblendet.

#### Löschbarkeit ("nicht löschbar"-Hinweis)

`GET /data/imports` liefert in der Praxis kein Status-Feld (nur `uuid`/`gdsKey`/`importDate`/`footprint`). Ein Package gilt daher als löschbar, solange kein explizit anderslautender Status vom Server zurückkommt – das eigentliche "muss Status 'Imported' haben"-Kriterium wird letztlich vom `DELETE`-Aufruf selbst geprüft; ein Fehlschlag erscheint dann als `[FAIL]` im Log mit der Original-Fehlermeldung des GDWH.

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

Nach jeder erfolgreichen Import-Löschung räumt das Tool zusätzlich einen eventuell noch vorhandenen DataPackage-Ordner im Ingest-Bucket auf (`DELETE /dataPackages/{id}`, dieselbe ID wie der Import). Das betrifft nur den Bucket-Ordner, nicht die bereits gelöschten GDWH-Daten – so verschwindet das Package auch aus der DataPackages-Ansicht im GDWH-Portal, und der Bucket ist frei für einen sauberen Neu-Import. Existiert kein Bucket-Ordner mehr (Normalfall, wenn er bereits automatisch geräumt wurde), wird das ohne Fehlermeldung übersprungen; schlägt das Aufräumen aus einem anderen Grund fehl, erscheint eine Warnung im Log — die eigentliche GDWH-Löschung ist davon nicht betroffen.

Nach Abschluss werden erfolgreich zum Löschen eingereichte DataPackages automatisch aus der Liste entfernt (kein manueller Reload nötig). Fehlgeschlagene Packages bleiben sichtbar und ausgewählt.

---

### Typischer Workflow GDWH — DataPackage entfernen

```
1.  Umgebung wählen (INT zum Testen, PROD für Live-Daten)
2.  GDS-Key eingeben  →  [Imports laden]
       → Liste wird mit Auftragstyp, AREA, Jahr, Commentary und Dateiformat angereichert
3.  Zu löschende DataPackages ankreuzen
4.  Optional: E-Mail für Job-Benachrichtigung eingeben
5.  [Import Auswahl (n) löschen]  →  Sicherheitsdialog bestätigen
6.  Job-ID aus dem Log notieren — Abschluss folgt per E-Mail oder direkt im GDWH prüfen
```

---

## Tests

```bash
pytest test_functions.py -v
```

113 Tests decken alle API-Funktionen in `stac_api.py` und `gdwh_api.py` ab (HTTP-Calls werden gemockt, inkl. GDWH über `_gdwh_session()`), u.a. `gdwh_import_footprint_bbox`, `gdwh_search_file_metadata`, `gdwh_cleanup_data_package`, `check_asset_info` sowie `list_asset_uploads`/`abort_asset_upload` (Upload-Recovery).

---

## Hinweise

- Der BVCOL-Firmenproxy (`proxy-bvcol.admin.ch:8080`) ist in `stac_api.py` und `gdwh_api.py` hinterlegt. `stac_api.py` versucht ihn zuerst und schaltet nach einem `ProxyError` automatisch auf Direktverbindung um — dadurch funktioniert das Tool auch ausserhalb des Bundesnetzes (z.B. privater Rechner), sofern der STAC-Endpunkt direkt erreichbar ist. Für abweichende Proxy-Konfigurationen: `secrets/proxy_config.json` anlegen (Vorlage: `secrets/proxy_config_template.json`).
- `logs/` enthält Tages-Logs und ist nicht im Git-Tracking.
- STAC-Endpunkte: swisstopo Transactional API (`DELETE /collections/{id}/items/{itemId}/assets/{assetKey}`, `DELETE /collections/{id}/items/{itemId}`) sowie die Upload-Extension (`GET`/`POST .../assets/{assetKey}/uploads[/{uploadId}/abort]`) für die "upload in progress"-Wiederherstellung
- GDWH-Endpunkte: GDWH-API v2 (`GET /api/geodatasets/{gdsKey}/data/imports`, `DELETE /api/geodatasets/{gdsKey}/data/imports/{datapackageId}`)
- Koordinaten im LV95-Format (CH1903+, EPSG:2056) mit Schweizer Apostroph als Tausendertrennzeichen
