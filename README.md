# STAC / GDWH Deleting-Tool

Kleines GUI-Tool, um Daten gezielt aus dem **Geodata-Warehouse (GDWH)** und aus **STAC** zu löschen — z.B. um einen Import vor einem Re-Import sauber zu entfernen.

## Starten

Eingabe im cmd-Terminal:
```bash
python pfad/0_GUI_stac_gdwh_delete_Data.py
```
<img width="713" height="676" alt="image" src="https://github.com/user-attachments/assets/6c11bb18-c7a4-4faf-8f76-7624d0f838a9" />


Das Fenster hat zwei Tabs:

| Tab | Löscht |
|---|---|
| **GDWH** [INT/PROD] | DataPackage-Imports aus dem Geodata-Warehouse (`ltgdwhi` / `ltgdwh`) |
| **STAC** [INT/PROD] | Assets (und leere Items) aus der Collection `ch.swisstopo.spezialbefliegungen` |

**Hintergrund:** Die Pipeline läuft GDWH → automatisierter STAC-Upload. Beim Re-Import müssen deshalb meist beide Systeme bereinigt werden.

---

## Voraussetzungen

- Python 3.6+, Pakete `requests` und `requests-negotiate-sspi` (werden beim Start automatisch nachinstalliert, falls sie fehlen)
- Für den STAC-Tab braucht das Script den `secrets/`-Ordner mit der Datei
  `secrets/stac_credentials.json` (Vorlage siehe unten). Diese Zugangsdaten
  müssen vom Benutzer bei TBKN/swisstopo angefragt werden — für GDWH reicht
  die Windows-Anmeldung
- GDWH ist nur im internen Netz / VPN erreichbar

```json
// secrets/stac_credentials.json
{
    "INT":  { "username": "...", "password": "..." },
    "PROD": { "username": "...", "password": "..." }
}
```

> `secrets/` ist per `.gitignore` von Git ausgeschlossen — Credentials nie committen.

---

## Tab GDWH — DataPackages löschen

1. Umgebung wählen (INT zum Testen, PROD für Live-Daten)
2. Auftragstyp (KRY/RAM/Alle) und Jahr wählen, GDS-Key wählen (z.B. `SB_DSM`, `SB_DOP`) → **Imports laden**
   Mit **Alle GDS** werden Imports über alle GDS-Keys hinweg geladen und in einer gemeinsamen Liste zusammengeführt — praktisch für einen GDS-Key-übergreifenden Überblick/Filter. Jede Zeile zeigt weiterhin ihren tatsächlichen GDS-Key an, die Löschung adressiert jedes Package korrekt mit seinem eigenen GDS-Key.
   Die Liste wird automatisch mit Auftragstyp, Area, Jahr und weiteren Infos angereichert. Auftragstyp und Jahr filtern die bereits geladene Liste sofort weiter, ohne Neu-Laden.

   **Frisch importiert vs. Anomalie:** GDWH indexiert seinen FileMetadata-Suchindex (Area/Jahr/Auftragstyp) zeitversetzt zum eigentlichen Import — live verifiziert am 2026-08-20, kann mehrere Stunden dauern. Ein Import ohne FileMetadata-Match wird deshalb zweigeteilt behandelt:
   - **Jünger als 24h:** gilt als „⏳ Frisch importiert“ (gelb), bleibt in der normalen Liste sichtbar, ist aber noch nicht auswählbar/löschbar, bis GDWH die Attribute nachgeliefert hat. Beim Laden erscheint dafür automatisch ein Hinweis-Popup.
   - **Älter als 24h:** gilt als echte **GDWH-Anomalie** (rot, `⚠ Kein FileMetadata-Match seit über 24h`) und deutet auf einen unsauberen GDWH-Zustand hin (z.B. eine frühere, unvollständige Löschung). Diese Zeilen werden über den Button „GDWH-Anomalien anzeigen (>24h ohne Daten)“ separat eingeblendet, damit sie nicht mit frischen Imports verwechselt werden. Über den `⧉ Kopieren`-Button neben dem Hinweis lässt sich die Import-UUID zur weiteren Recherche in die Zwischenablage kopieren.
3. Gewünschte Packages ankreuzen
4. Optional: E-Mail-Adresse für die Job-Benachrichtigung
5. **Import Auswahl löschen** → Sicherheitsabfrage bestätigen

Die Löschung läuft asynchron als Job im GDWH und passiert in drei Schritten:

1. **Alle** Lösch-Jobs werden zuerst gestartet (`DELETE /api/geodatasets/{gdsKey}/data/imports/{id}`) — bei einer Batch-Löschung wartet das Tool nicht seriell auf einen Job, bevor der nächste startet.
2. Alle gestarteten Jobs werden anschliessend gemeinsam/interleaved verfolgt (`GET /api/jobs/{jobId}`, alle 4s), bis jeder einen Endstatus meldet oder nach 300s pro Job das Timeout erreicht ist. Live-Fortschritt in der Statusanzeige, Details im Log.
3. Erst nach bestätigtem Job-Erfolg räumt das Tool zusätzlich den Ingest-Bucket auf (`DELETE .../dataPackages/{id}`) — so wird der Bucket nicht während eines noch laufenden Lösch-Jobs angerührt.

Jedes Package landet danach in einer von drei Kategorien: **Erfolgreich bestätigt** (Job-Status meldet Erfolg), **Fehlgeschlagen** (DELETE-Request oder Job selbst melden einen Fehler) oder **Status unklar/Timeout** (kein eindeutiger Endstatus innert 300s — bleibt bewusst in der Liste stehen, kein automatischer Bucket-Cleanup, muss manuell per Log/E-Mail/GDWH-Portal geprüft werden).

---

## Tab STAC — Assets löschen

1. Umgebung wählen, **Credentials laden**
2. Auftragstyp (KRY/RAM) wählen, Item-ID oder Suchbegriff eingeben (z.B. Datum) → **Laden**
3. Optional **Assets prüfen (HEAD)**, um kaputte/fehlende Dateien (rot markiert) zu finden — dann **Fehlerhafte auswählen**
4. Assets ankreuzen und **Asset Auswahl löschen** → Sicherheitsabfrage bestätigen

Wird durch die Löschung ein Item komplett leer, entfernt das Tool es automatisch mit. Erfolgreich gelöschte Einträge verschwinden direkt aus der Liste, fehlgeschlagene bleiben sichtbar für einen erneuten Versuch.

---

## Tests

```bash
pytest test_functions.py -v
```

---

## Hinweise

- Läuft normalerweise über den Bundes-Proxy, fällt bei Bedarf automatisch auf Direktverbindung zurück (z.B. für private Rechner) — abweichende Proxy-Config über `secrets/proxy_config.json` möglich.
- `logs/` enthält Tages-Logs und ist nicht in Git.
- Koordinaten im LV95-Format (EPSG:2056).
