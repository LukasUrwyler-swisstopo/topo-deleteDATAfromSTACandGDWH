# STAC / GDWH Deleting-Tool

Kleines GUI-Tool, um Daten gezielt aus dem **Geodata-Warehouse (GDWH)** und aus **STAC** zu löschen — z.B. um einen Import vor einem Re-Import sauber zu entfernen.

<img width="958" height="1027" alt="grafik" src="https://github.com/user-attachments/assets/618f4c2e-ab3b-4285-a292-49123efd2ca8" />

## Starten

```bash
python 0_GUI_stac_gdwh_delete_Data.py
```

Das Fenster hat zwei Tabs:

| Tab | Löscht |
|---|---|
| **GDWH** [INT/PROD] | DataPackage-Imports aus dem Geodata-Warehouse (`ltgdwhi` / `ltgdwh`) |
| **STAC** [INT/PROD] | Assets (und leere Items) aus der Collection `ch.swisstopo.spezialbefliegungen` |

**Hintergrund:** Die Pipeline läuft GDWH → automatisierter STAC-Upload. Beim Re-Import müssen deshalb meist beide Systeme bereinigt werden.

---

## Voraussetzungen

- Python 3.6+, Pakete `requests` und `requests-negotiate-sspi` (werden beim Start automatisch nachinstalliert, falls sie fehlen)
- STAC-Zugangsdaten in `secrets/stac_credentials.json` (Vorlage siehe unten) — für GDWH reicht die Windows-Anmeldung
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
2. Auftragstyp (KRY/RAM/Alle) und Jahr wählen, GDS-Key eingeben (z.B. `SB_DSM`, `SB_DOP`) → **Imports laden**
   Die Liste wird automatisch mit Auftragstyp, Area, Jahr und weiteren Infos angereichert. Auftragstyp und Jahr filtern die bereits geladene Liste sofort weiter, ohne Neu-Laden.
3. Gewünschte Packages ankreuzen
4. Optional: E-Mail-Adresse für die Job-Benachrichtigung
5. **Import Auswahl löschen** → Sicherheitsabfrage bestätigen

Die Löschung läuft asynchron als Job im GDWH; Fortschritt/Abschluss siehe Log bzw. E-Mail.

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
