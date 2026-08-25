"""
0_GUI_stac_gdwh_delete_Data.py  –  STAC / GDWH Deleting-Tool

Tab 1 – STAC Assets:
  Löscht Assets/Items aus der Collection "ch.swisstopo.spezialbefliegungen".
  Credentials: secrets/stac_credentials.json
  Format: {"INT": {"username": "...", "password": "..."}, "PROD": {...}}

Tab 2 – GDWH Imports:
  Löscht DataPackages (Imports) aus dem GDWH.
  Credentials: secrets/gdwh_credentials.json
  Format: {"INT": {"username": "...", "password": "..."}, "PROD": {...}}

Autor: (basierend auf util_stac_delete_ram.py von David Oesch)
Datum: 2025-12
Lizenz: MIT
"""

import re
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import ctypes
import threading
import concurrent.futures
import json
import logging
import webbrowser
from email.utils import parsedate
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

from stac_api import (
    COLLECTION_ID, ENVIRONMENTS, AUFTRAGSTYPEN, EXT_PRESETS,
    get_item_direct, get_collection_items,
    delete_asset, delete_item, check_asset_info, asset_area, browser_url,
    stac_item_year, stac_item_area, stac_item_acq_date,
    list_asset_uploads, abort_asset_upload,
)
from gdwh_api import (
    GDWH_ENVIRONMENTS, GDWH_GDS_KEYS,
    gdwh_get_imports, gdwh_delete_import, gdwh_cleanup_data_package,
    gdwh_wait_for_jobs, GDWH_JOB_STATUS_SUCCESS, GDWH_JOB_STATUS_FAILURE,
    gdwh_import_id, gdwh_import_date, gdwh_import_status,
    gdwh_import_footprint_bbox,
    gdwh_search_file_metadata, gdwh_index_file_metadata_by_import,
)

# Sentinel im GDS-Key-Dropdown: lädt/filtert Imports über alle GDS_KEYS hinweg
# statt nur einen einzelnen. Kein echter GDS-Key, daher klar als Auswahl-Option
# von GDWH_GDS_KEYS abgesetzt (nie an die GDWH-API übergeben).
GDWH_ALL_GDS_OPTION = "Alle GDS"

# GDWH indexiert den fileMetadata-Suchindex (Area/Jahr/Auftragstyp) zeitversetzt
# zum eigentlichen Import – live verifiziert am 2026-08-20: ein Import von
# vor >1h hatte noch keinen Match, alle älteren Imports desselben Tages
# (ab ca. 4h zuvor) bereits. Innerhalb dieses Fensters ist "kein Match" also
# normal und KEINE Anomalie. Erst danach gilt ein fehlender Match als
# vermutlich verwaister Alt-Eintrag (siehe _gdwh_apply_filter).
_GDWH_PENDING_HOURS = 24

# Polling des Lösch-Jobs (GET /api/jobs/{jobId}) nach dem DELETE-Aufruf:
# solange warten bis der Job einen terminalen Status meldet (Erfolg/Fehler),
# max. jedoch _GDWH_JOB_POLL_TIMEOUT Sekunden pro Package, bevor mit
# unbekanntem Status weitergefahren wird (siehe gdwh_wait_for_job).
_GDWH_JOB_POLL_TIMEOUT  = 300.0
_GDWH_JOB_POLL_INTERVAL = 4.0


def _gdwh_is_pending(imp: Dict) -> bool:
    """True, wenn ein Import jünger als _GDWH_PENDING_HOURS ist. Für so junge
    Imports ist ein fehlender FileMetadata-Match durch die Indexierungs-
    Verzögerung von GDWH erklärt, nicht durch eine echte Anomalie."""
    try:
        imp_dt = datetime.strptime(gdwh_import_date(imp), "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    return datetime.now() - imp_dt < timedelta(hours=_GDWH_PENDING_HOURS)

# ─── Farbpaletten ─────────────────────────────────────────────────────────────

LIGHT = {
    "root":      "#f0f0f0",
    "panel":     "#f5f5f5",
    "input":     "#ffffff",
    "fg":        "#1a1a1a",
    "fg_dim":    "#666666",
    "accent":    "#0063b1",
    "hdr_bg":    "#1a3a5c",
    "hdr_fg":    "#ffffff",
    "btn":       "#e1e1e1",
    "btn_hover": "#c8c8c8",
    "list":      "#ffffff",
    "log_bg":    "#1e1e1e",
    "log_fg":    "#d4d4d4",
    "sep":       "#c0c0c0",
    "sel_bg":    "#0078d4",
    "sel_fg":    "#ffffff",
    "ok":        "#2e7d32",
    "err":       "#c62828",
    "hint":      "#8a6f2e",
    "chk_item":  "#0063b1",
    "chk_bg":    "#ffffff",
    "chk_row":   "#f9f9f9",
}

DARK = {
    "root":      "#1e1e1e",
    "panel":     "#252526",
    "input":     "#3c3c3c",
    "fg":        "#cccccc",
    "fg_dim":    "#7a7a7a",
    "accent":    "#4fc3f7",
    "hdr_bg":    "#1a1a1a",
    "hdr_fg":    "#cccccc",
    "btn":       "#3c3c3c",
    "btn_hover": "#505050",
    "list":      "#2d2d30",
    "log_bg":    "#1e1e1e",
    "log_fg":    "#d4d4d4",
    "sep":       "#3c3c3c",
    "sel_bg":    "#094771",
    "sel_fg":    "#cccccc",
    "ok":        "#66bb6a",
    "err":       "#ef5350",
    "hint":      "#c9a84c",
    "chk_item":  "#4fc3f7",
    "chk_bg":    "#2d2d30",
    "chk_row":   "#303030",
}


# ─── Hilfsfunktionen (Formatierung Tree-Spalten) ──────────────────────────────

def _fmt_size(size_bytes: Optional[int]) -> str:
    if size_bytes is None:
        return "–"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    return f"{size_bytes / 1024 ** 3:.2f} GB"


def _fmt_date(lm_str: Optional[str]) -> str:
    """Parst HTTP Last-Modified-Header auf YYYY-MM-DD."""
    if not lm_str:
        return "–"
    try:
        t = parsedate(lm_str)
        if t:
            return f"{t[0]}-{t[1]:02d}-{t[2]:02d}"
    except Exception:
        pass
    return lm_str[:10] if len(lm_str) >= 10 else lm_str


def _status_label(sc: Optional[int]) -> Tuple[str, str]:
    """Gibt (Anzeigetext, Tag-Name) für einen HTTP-Statuscode zurück."""
    if sc is None:
        return "–", "asset_dim"
    if sc == 200:
        return "✓  200", "asset_ok"
    if sc == -4:
        return "✓  >50GB", "asset_ok"
    if sc > 0:
        return f"✗  {sc}", "asset_err"
    if sc == -2:
        return "✗  timeout", "asset_warn"
    return "✗  err", "asset_warn"


# ─── Bestätigungs-Dialog (STAC) ───────────────────────────────────────────────

class _BaseConfirmDialog(tk.Toplevel):
    """Gemeinsame Basis für die Löschbestätigungs-Dialoge (STAC & GDWH).

    Unterklassen liefern Titel und Info-Text über _title()/_info_text();
    Aufbau, Bestätigungs-Checkbox/-Eingabe und Zustandslogik sind identisch.
    """

    def __init__(self, parent, environment: str, dark: bool):
        super().__init__(parent)
        self.result       = False
        self._environment = environment
        T = DARK if dark else LIGHT
        self.title(self._title())
        self.resizable(False, False)
        self.configure(bg=T["root"])
        self.grab_set()
        self.focus_set()
        self._build(T, environment)
        self.transient(parent)
        self.wait_window(self)

    def _title(self) -> str:
        raise NotImplementedError

    def _info_text(self, env: str) -> str:
        raise NotImplementedError

    def _build(self, T, env):
        hdr = tk.Frame(self, bg=T["err"], pady=6)
        hdr.pack(fill="x")
        tk.Label(hdr, text="  WARNUNG – DIESE AKTION IST NICHT UMKEHRBAR  ",
                 bg=T["err"], fg="#ffffff", font=("Segoe UI", 11, "bold")).pack()

        body = tk.Frame(self, bg=T["root"], padx=20, pady=10)
        body.pack(fill="both")

        tk.Label(body, text=self._info_text(env), bg=T["root"], fg=T["fg"],
                 font=("Segoe UI", 10), justify="left").pack(anchor="w", pady=(6, 10))

        tk.Frame(body, bg=T["sep"], height=1).pack(fill="x", pady=6)

        self._check_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            body,
            text="Ich verstehe, dass die Daten permanent und unwiderruflich gelöscht werden.",
            variable=self._check_var, command=self._update_state,
            bg=T["root"], fg=T["fg"], selectcolor=T["input"],
            activebackground=T["root"], activeforeground=T["fg"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=4)

        tk.Label(body, text=f'\nZur Bestätigung den Umgebungsnamen tippen ("{env}"):',
                 bg=T["root"], fg=T["hint"],
                 font=("Segoe UI", 9, "italic")).pack(anchor="w", padx=4)

        self._env_var   = tk.StringVar()
        self._env_entry = tk.Entry(
            body, textvariable=self._env_var, width=16, state="disabled",
            bg=T["input"], fg=T["fg"], insertbackground=T["fg"],
            disabledbackground=T["sep"], disabledforeground=T["fg_dim"],
            font=("Segoe UI", 10),
        )
        self._env_entry.pack(anchor="w", padx=4, pady=(3, 12))
        self._env_var.trace_add("write", lambda *_: self._update_state())

        btn_row = tk.Frame(body, bg=T["root"])
        btn_row.pack(fill="x", pady=(4, 6))
        tk.Button(btn_row, text="Abbrechen",
                  bg=T["btn"], fg=T["fg"], activebackground=T["btn_hover"],
                  activeforeground=T["fg"], font=("Segoe UI", 10), relief="flat",
                  padx=14, pady=6, command=self.destroy).pack(side="right", padx=(8, 0))
        self._ok_btn = tk.Button(
            btn_row, text="JETZT LÖSCHEN",
            bg=T["err"], fg="#ffffff", activebackground="#b71c1c",
            activeforeground="#ffffff", font=("Segoe UI", 10, "bold"),
            relief="flat", padx=14, pady=6, state="disabled", command=self._confirm,
        )
        self._ok_btn.pack(side="right")

    def _update_state(self):
        checked = self._check_var.get()
        self._env_entry.config(state="normal" if checked else "disabled")
        env_ok = self._env_var.get().strip().upper() == self._environment.upper()
        self._ok_btn.config(state="normal" if (checked and env_ok) else "disabled")

    def _confirm(self):
        self.result = True
        self.destroy()


class ConfirmDialog(_BaseConfirmDialog):
    def __init__(self, parent, environment: str, item_count: int,
                 asset_count: int, items_fully_deleted: int, dark: bool):
        self._item_count          = item_count
        self._asset_count         = asset_count
        self._items_fully_deleted = items_fully_deleted
        super().__init__(parent, environment, dark)

    def _title(self) -> str:
        return "Löschung bestätigen"

    def _info_text(self, env: str) -> str:
        if self._items_fully_deleted > 0:
            item_note = (
                f"davon {self._items_fully_deleted} Item(s) vollständig leer →\n"
                "  werden ebenfalls gelöscht. Restliche Items bleiben erhalten."
            )
        else:
            item_note = "Die Items selbst bleiben erhalten."
        return (f"Umgebung:              {env}\n"
                f"Collection:            {COLLECTION_ID}\n"
                f"Betroffene Items:      {self._item_count}\n"
                f"Assets zum Löschen:   {self._asset_count}\n\n"
                f"{item_note}\n"
                "Assets werden permanent gelöscht.")


# ─── Bestätigungs-Dialog (GDWH) ──────────────────────────────────────────────

class GDWHConfirmDialog(_BaseConfirmDialog):
    def __init__(self, parent, environment: str, gds_key: str,
                 pkg_count: int, dark: bool):
        self._gds_key   = gds_key
        self._pkg_count = pkg_count
        super().__init__(parent, environment, dark)

    def _title(self) -> str:
        return "GDWH Löschung bestätigen"

    def _info_text(self, env: str) -> str:
        return (f"Umgebung:                    {env}\n"
                f"GDS-Key:                     {self._gds_key}\n"
                f"DataPackages zum Löschen:   {self._pkg_count}\n\n"
                "Alle Daten der ausgewählten DataPackages\n"
                "werden permanent und unwiderruflich gelöscht.\n"
                "Die Löschung im GDWH ist asynchron (Job wird gestartet).")


# ─── Haupt-GUI ────────────────────────────────────────────────────────────────

class KryDeleteApp(tk.Tk):

    _COLS      = ("sel", "area", "status", "typ", "groesse", "geaendert")
    _COL_HEADS = {"sel": "Auswahl", "area": "Area", "status": "Status", "typ": "Typ / Ext.",
                  "groesse": "Grösse", "geaendert": "Geändert"}
    _COL_W     = {"sel": 60, "area": 90, "status": 170, "typ": 90,
                  "groesse": 90, "geaendert": 105}

    _CHK_ON      = "⬤"
    _CHK_OFF     = "◯"
    _CHK_PARTIAL = "◐"

    _LOAD_BTN_LABEL        = "ITEM-Liste laden"
    _LOAD_BTN_LABEL_RELOAD = "ITEM-Liste aktualisieren"
    _SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    _SHOW_FAULTY_BTN_LABEL   = "Fehlerhafte anzeigen"
    _SHOW_NO_THUMB_BTN_LABEL = "ITEMs ohne Thumbnail"
    _SHOW_ALL_BTN_LABEL      = "Alle Assets wieder anzeigen"
    # Items/Assets mit dieser Zeichenfolge im Namen haben planmässig nie ein
    # Thumbnail (z.B. Übersichts-/Mosaik-Items) – im "ITEMs ohne Thumbnail"-
    # Filter sind sie keine echten Kandidaten und sollen nicht auftauchen.
    _NO_THUMB_EXCLUDE_SUBSTR = "t23595900"

    _GDWH_FETCH_BTN_LABEL        = "Imports laden"
    _GDWH_FETCH_BTN_LABEL_RELOAD = "Imports aktualisieren"
    _GDWH_LEICHEN_BTN_LABEL      = f"GDWH-Anomalien anzeigen (>{_GDWH_PENDING_HOURS}h ohne Daten)"
    _GDWH_LEICHEN_BTN_LABEL_BACK = "Zurück zur normalen Ansicht"

    def __init__(self):
        super().__init__()
        self.title("STAC / GDWH Deleting-Tool  —  ch.swisstopo.spezialbefliegungen")
        self.minsize(960, 740)

        self._dark: bool = True

        # STAC State
        self._auth: Optional[Tuple] = None
        self._base_url: str = ""
        self._items_preview: List[Dict] = []
        self._items_asset_hrefs: Dict[str, Dict[str, str]] = {}
        self._items_assets: Dict[str, List[str]] = {}
        # Baum-Metadaten: tree_iid → dict mit kind/item_id/asset_key/href/item
        self._nodes: Dict[str, Dict] = {}
        # Auswahl zum Löschen je Asset-Knoten (tree_iid → bool). Default = False
        # (bewusstes Opt-in, anders als beim read-only Monitor-Tool).
        self._checked: Dict[str, bool] = {}
        # Auswahl zum Löschen für Items OHNE Assets (item_id → bool) – solche
        # Items haben keine Asset-Kindknoten, die Checkbox sitzt daher direkt
        # auf der Item-Zeile.
        self._checked_items: Dict[str, bool] = {}
        # Letztes Prüfergebnis je Asset (item_id, asset_key) → Info-Dict mit
        # tag/status_text/size_text/date_text. Überlebt einen Tree-Rebuild
        # (Filterwechsel/Toggle "Nur Fehlerhafte"), da _populate_tree() den
        # Tree bei jedem Aufruf komplett neu aufbaut (siehe _clear_tree()).
        self._asset_status: Dict[Tuple[str, str], Dict] = {}
        # Toggle für "Fehlerhafte anzeigen" (fehlerhafte Assets + leere Items)
        self._show_faulty_only: bool = False
        # Toggle für "ITEMs ohne Thumbnail" (nur bei Auftragstyp RAM sichtbar)
        self._show_no_thumb_only: bool = False

        # Lade-Spinner im "ITEM-Liste laden"-Button
        self._spinner_job: Optional[str] = None
        self._spinner_idx: int = 0
        # Kippt auf True, sobald zum ersten Mal Items im Tree angezeigt wurden
        # (nicht schon beim reinen Fetch) – danach zeigt der Button dauerhaft
        # die "aktualisieren"-Variante. Reset bei Umgebungswechsel.
        self._items_loaded_once: bool = False

        # GDWH State
        self._gdwh_base_url: str = GDWH_ENVIRONMENTS["INT"]
        self._gdwh_imports: List[Dict] = []
        self._gdwh_selection: Dict[str, tk.BooleanVar] = {}
        # Container-Frame je DataPackage-Zeile (pkg_id → Frame), damit einzelne
        # Zeilen nach erfolgreicher Löschung gezielt entfernt werden können.
        self._gdwh_row_widgets: Dict[str, tk.Frame] = {}
        # Analog zu self._items_loaded_once, für den "Imports laden"-Button.
        self._gdwh_loaded_once: bool = False

        self._file_logger = self._setup_file_logger()
        self._build_ui()
        self._apply_theme(True)

    # ── File-Logger Setup ─────────────────────────────────────────────────────

    def _setup_file_logger(self) -> logging.Logger:
        log_dir = Path(__file__).parent / "logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"stac_gdwh_delete_{datetime.now().strftime('%Y-%m-%d')}.log"
        logger = logging.getLogger("stac_gdwh_delete_file")
        logger.setLevel(logging.DEBUG)
        if not logger.handlers:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            logger.addHandler(fh)
        return logger

    def _make_session_logger(self, mode: str, env: str,
                              year: str, area: str, stac_dt: str) -> logging.Logger:
        """Erstellt pro Lösch-Vorgang einen Logger mit beschreibendem Dateinamen.
        Format: LOG_STAC_INT_2024_ALETSCH_2024-08-20.log"""
        def _s(s: str) -> str:
            return re.sub(r"[^\w\-]", "_", s).strip("_") or ""
        dt_short = stac_dt[:10] if stac_dt else ""
        parts    = [p for p in ["LOG", mode, env, year, _s(area), _s(dt_short)] if p]
        log_name = "_".join(parts)
        log_dir  = Path(__file__).parent / "logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"{log_name}.log"
        logger   = logging.getLogger(f"session_{log_name}")
        logger.setLevel(logging.DEBUG)
        if not logger.handlers:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            logger.addHandler(fh)
        return logger

    # ── UI aufbauen ───────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        self._hdr = tk.Frame(self, height=52)
        self._hdr.pack(fill="x")
        self._hdr.pack_propagate(False)
        self._hdr_lbl = tk.Label(
            self._hdr,
            text="  STAC / GDWH Deleting-Tool  —  ch.swisstopo.spezialbefliegungen",
            font=("Segoe UI", 13, "bold"),
        )
        self._hdr_lbl.pack(side="left", padx=16, pady=10)
        self._theme_btn = tk.Button(
            self._hdr, text="Hell", relief="flat", borderwidth=0,
            font=("Segoe UI", 9), cursor="hand2", padx=10, pady=4,
            command=self._toggle_theme,
        )
        self._theme_btn.pack(side="right", padx=12)

        # Notebook mit 2 Tabs
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill="both", expand=True, padx=12, pady=8)
        self._nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ── Tab 1: STAC ───────────────────────────────────────────────────────
        stac_tab = ttk.Frame(self._nb)
        self._nb.add(stac_tab, text="  STAC  Assets  ")

        outer = ttk.Frame(stac_tab)
        outer.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(outer, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._sf = ttk.Frame(self._canvas)
        win_id   = self._canvas.create_window((0, 0), window=self._sf, anchor="nw")
        self._sf.bind("<Configure>",
                      lambda _: self._canvas.configure(
                          scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfig(win_id, width=e.width))
        self._canvas.bind_all("<MouseWheel>",
                              lambda e: self._canvas.yview_scroll(
                                  -1 * (e.delta // 120), "units"))

        self._build_step1(self._sf)
        self._build_step2(self._sf)
        self._build_actions(self._sf)
        self._build_step3(self._sf)
        self._build_step4(self._sf)

        # ── Tab 2: GDWH ───────────────────────────────────────────────────────
        gdwh_tab = ttk.Frame(self._nb)
        self._nb.add(gdwh_tab, text="  GDWH  Imports  ")
        self._build_gdwh_tab(gdwh_tab)

        # GDWH-Tab wird häufiger genutzt als STAC Assets – vor STAC einreihen
        # und beim Start auch direkt anzeigen (sonst bleibt trotz Neueinreihung
        # der zuerst hinzugefügte STAC-Tab als aktive Auswahl stehen).
        self._nb.insert(0, gdwh_tab)
        self._nb.select(gdwh_tab)

    def _on_tab_changed(self, _):
        """Mausrad-Scrollziel je nach aktivem Tab umschalten."""
        tab_text = self._nb.tab(self._nb.select(), "text")
        if "STAC" in tab_text:
            self._canvas.bind_all(
                "<MouseWheel>",
                lambda e: self._canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        else:
            self._gdwh_canvas.bind_all(
                "<MouseWheel>",
                lambda e: self._gdwh_canvas.yview_scroll(-1 * (e.delta // 120), "units"))

    # ═══════════════════════════════════════════════════════════════════════════
    # STAC Tab – Schritte 1–4
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_step1(self, parent):
        sec = ttk.LabelFrame(parent, text="1   Umgebung & Credentials",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(7, weight=1)

        ttk.Label(sec, text="Umgebung:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._env_var = tk.StringVar(value="INT")
        self._env_radios: List[ttk.Radiobutton] = []
        for col, env in enumerate(("INT", "PROD"), 1):
            rb = ttk.Radiobutton(sec, text=env, variable=self._env_var, value=env,
                                  command=self._on_env_change)
            rb.grid(row=0, column=col, sticky="w", padx=4)
            self._env_radios.append(rb)

        self._cred_btn = ttk.Button(sec, text="Credentials laden",
                                     command=self._load_credentials,
                                     style="Amber.TButton")
        self._cred_btn.grid(row=0, column=3, padx=(12, 8))

        self._cred_status = ttk.Label(sec, text="nicht geladen",
                                       font=("Segoe UI", 9, "italic"),
                                       style="Dim.TLabel")
        self._cred_status.grid(row=0, column=4, padx=(0, 12))

        ttk.Button(sec, text="STAC Browser öffnen",
                   command=self._open_stac_browser).grid(row=0, column=5, padx=(0, 12))

        self._url_lbl = ttk.Label(sec, text=ENVIRONMENTS["INT"],
                                   font=("Segoe UI", 8), style="Dim.TLabel")
        self._url_lbl.grid(row=0, column=6, sticky="w", padx=(0, 8))

    def _build_step2(self, parent):
        sec = ttk.LabelFrame(parent, text="2   Auftragstyp, Item & Asset-Filter",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(1, weight=1)

        ttk.Label(sec, text="Auftragstyp:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._auftragstyp_var = tk.StringVar(value=list(AUFTRAGSTYPEN.keys())[0])
        typ_frame = ttk.Frame(sec)
        typ_frame.grid(row=0, column=1, columnspan=3, sticky="w")
        for typ in AUFTRAGSTYPEN:
            ttk.Radiobutton(typ_frame, text=typ, variable=self._auftragstyp_var, value=typ,
                            command=self._on_auftragstyp_change).pack(side="left", padx=(0, 14))

        ttk.Label(sec, text="Jahr:").grid(row=1, column=0, sticky="w",
                                          padx=(0, 8), pady=(6, 0))
        self._year_filter_var = tk.StringVar()
        self._year_filter_var.trace_add("write", lambda *_: self._apply_filters())
        ttk.Entry(sec, textvariable=self._year_filter_var, width=8).grid(
            row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(
            sec, text="z.B. 2023  —  Leer = alle Jahre",
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=1, column=2, sticky="w", pady=(6, 0))

        ttk.Label(sec, text="Area:").grid(row=2, column=0, sticky="w",
                                           padx=(0, 8), pady=(6, 0))
        self._area_filter_var = tk.StringVar()
        self._area_filter_var.trace_add("write", lambda *_: self._apply_filters())
        ttk.Entry(sec, textvariable=self._area_filter_var, width=46).grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=(0, 10), pady=(6, 0))

        ttk.Label(sec, text="Item-ID:").grid(row=3, column=0, sticky="w",
                                              padx=(0, 8), pady=(6, 0))
        self._item_id_var = tk.StringVar(value="")
        self._item_id_var.trace_add("write", lambda *_: self._apply_filters())
        ttk.Entry(sec, textvariable=self._item_id_var, width=46).grid(
            row=3, column=1, columnspan=2, sticky="ew", padx=(0, 10), pady=(6, 0))
        ttk.Label(
            sec, text="Teilstring genügt  —  filtert die geladene Liste  —  Leer = alle Items",
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=3, column=3, sticky="w", pady=(6, 0))

        ttk.Label(sec, text="Asset-Key:").grid(row=4, column=0, sticky="w",
                                                padx=(0, 8), pady=(6, 0))
        self._asset_filter_var = tk.StringVar()
        self._asset_filter_var.trace_add("write", lambda *_: self._apply_filters())
        ttk.Entry(sec, textvariable=self._asset_filter_var, width=30).grid(
            row=4, column=1, sticky="w", padx=(0, 10), pady=(6, 0))
        ttk.Label(
            sec, text='Teilstring, z.B. "nrgb" oder "16bit"  —  Leer = alle Assets',
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=4, column=2, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Label(sec, text="Dateiendung:").grid(row=5, column=0, sticky="w",
                                                  padx=(0, 8), pady=(6, 0))
        ext_frame = ttk.Frame(sec)
        ext_frame.grid(row=5, column=1, columnspan=3, sticky="w", pady=(6, 0))

        self._ext_vars: List[Tuple[tk.BooleanVar, List[str]]] = []
        for label, exts in EXT_PRESETS:
            var = tk.BooleanVar(value=False)
            var.trace_add("write", lambda *_: self._apply_filters())
            self._ext_vars.append((var, exts))
            ttk.Checkbutton(ext_frame, text=label, variable=var).pack(
                side="left", padx=(0, 10))

        ttk.Label(ext_frame, text="Frei:").pack(side="left", padx=(6, 4))
        self._ext_custom_var = tk.StringVar()
        self._ext_custom_var.trace_add("write", lambda *_: self._apply_filters())
        ttk.Entry(ext_frame, textvariable=self._ext_custom_var, width=16).pack(side="left")
        ttk.Label(ext_frame, text="z.B. gpkg pdf",
                  font=("Segoe UI", 8, "italic"), style="Dim.TLabel").pack(
                      side="left", padx=(4, 0))

    def _build_actions(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 6))

        self._load_btn = ttk.Button(
            row, text=self._LOAD_BTN_LABEL, command=self._load, state="disabled",
            style="AmberBold.TButton")
        self._load_btn.pack(side="left", padx=(0, 16))

        ttk.Separator(row, orient="vertical").pack(side="left", fill="y", padx=(0, 16))

        self._expand_btn = ttk.Button(
            row, text="Alle aufklappen", command=self._expand_all, state="disabled")
        self._expand_btn.pack(side="left", padx=(0, 4))

        self._collapse_btn = ttk.Button(
            row, text="Alle einklappen", command=self._collapse_all, state="disabled")
        self._collapse_btn.pack(side="left")

    def _build_step3(self, parent):
        sec = ttk.LabelFrame(parent, text="3   Assets auswählen zum Löschen",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(0, weight=1)

        sel_row = ttk.Frame(sec)
        sel_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self._sel_all_btn = ttk.Button(
            sel_row, text="Alle auswählen",
            command=self._select_all_assets, state="disabled",
        )
        self._sel_all_btn.pack(side="left", padx=(0, 4))

        self._sel_none_btn = ttk.Button(
            sel_row, text="Alle abwählen",
            command=self._deselect_all_assets, state="disabled",
        )
        self._sel_none_btn.pack(side="left", padx=(0, 16))

        ttk.Separator(sel_row, orient="vertical").pack(side="left", fill="y", padx=(0, 16))

        self._check_btn = ttk.Button(
            sel_row, text="Assets prüfen (HEAD)",
            command=self._check_assets, state="disabled",
            style="Amber.TButton",
        )
        self._check_btn.pack(side="left", padx=(0, 4))

        self._show_faulty_btn = ttk.Button(
            sel_row, text=self._SHOW_FAULTY_BTN_LABEL,
            command=self._toggle_faulty_filter, state="disabled",
        )
        self._show_faulty_btn.pack(side="left", padx=(0, 4))

        self._sel_faulty_btn = ttk.Button(
            sel_row, text="Fehlerhafte auswählen",
            command=self._select_faulty_assets, state="disabled",
        )
        self._sel_faulty_btn.pack(side="left", padx=(0, 16))

        # Nur bei Auftragstyp RAM relevant (Thumbnail-Pflicht) – wird erst
        # sichtbar gepackt, wenn AUFTRAGSTYPEN[...] == "ram" ist, siehe
        # _update_no_thumb_btn_visibility(). "sel_row" bleibt so kompakt
        # für KRY/Alle, wo dieser Filter keinen Sinn ergibt.
        self._show_no_thumb_btn = ttk.Button(
            sel_row, text=self._SHOW_NO_THUMB_BTN_LABEL,
            command=self._toggle_no_thumb_filter, state="disabled",
        )

        tree_outer = ttk.Frame(sec)
        tree_outer.grid(row=1, column=0, sticky="nsew", pady=(0, 4))
        tree_outer.rowconfigure(0, weight=1)
        tree_outer.columnconfigure(0, weight=1)
        sec.rowconfigure(1, weight=1)

        self._tree = ttk.Treeview(
            tree_outer, columns=self._COLS, show="tree headings",
            selectmode="browse", height=12)

        self._tree.column("#0", width=320, minwidth=200, stretch=False)
        self._tree.heading("#0", text="Item / Asset")
        for col in self._COLS:
            self._tree.column(col, width=self._COL_W[col],
                              minwidth=55, stretch=False, anchor="center")
            self._tree.heading(col, text=self._COL_HEADS[col])

        vsb = ttk.Scrollbar(tree_outer, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_outer, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self._ctx = tk.Menu(self, tearoff=0)
        self._tree.bind("<Button-3>", self._on_right_click)
        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.bind("<Button-1>", self._on_tree_click)

        self._tree.bind("<Enter>", lambda _: self._tree.bind_all(
            "<MouseWheel>",
            lambda e: self._tree.yview_scroll(-1 * (e.delta // 120), "units")))
        self._tree.bind("<Leave>", lambda _: self._canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._canvas.yview_scroll(-1 * (e.delta // 120), "units")))

        self._preview_lbl = ttk.Label(
            sec, text="Noch keine Vorschau geladen.",
            font=("Segoe UI", 9, "italic"), style="Dim.TLabel",
        )
        self._preview_lbl.grid(row=2, column=0, sticky="w", pady=(2, 0))

        self._update_no_thumb_btn_visibility()

    def _build_step4(self, parent):
        sec = ttk.LabelFrame(parent, text="4   Löschung ausführen",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="both", expand=True, pady=(0, 4))
        sec.columnconfigure(0, weight=1)
        sec.rowconfigure(0, weight=1)

        self._log = scrolledtext.ScrolledText(
            sec, height=10, state="disabled",
            font=("Cascadia Mono", 9), wrap="word",
        )
        self._log.grid(row=0, column=0, sticky="nsew", pady=(0, 8))

        btn_row = ttk.Frame(sec)
        btn_row.grid(row=1, column=0, sticky="ew")

        self._del_btn = tk.Button(
            btn_row, text="Ausgewählte Assets löschen …",
            relief="flat", padx=14, pady=6,
            font=("Segoe UI", 10, "bold"), state="disabled",
            command=self._start_deletion,
        )
        self._del_btn.pack(side="left")

        self._progress = ttk.Progressbar(btn_row, mode="determinate", length=280)
        self._progress.pack(side="left", padx=16)

        self._status_lbl = ttk.Label(btn_row, text="", font=("Segoe UI", 9))
        self._status_lbl.pack(side="left")

    # ═══════════════════════════════════════════════════════════════════════════
    # GDWH Tab – Schritte 1–4
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_gdwh_tab(self, parent):
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)

        self._gdwh_canvas = tk.Canvas(outer, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=self._gdwh_canvas.yview)
        self._gdwh_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._gdwh_canvas.pack(side="left", fill="both", expand=True)

        self._gdwh_sf = ttk.Frame(self._gdwh_canvas)
        win_id = self._gdwh_canvas.create_window((0, 0), window=self._gdwh_sf, anchor="nw")
        self._gdwh_sf.bind(
            "<Configure>",
            lambda _: self._gdwh_canvas.configure(
                scrollregion=self._gdwh_canvas.bbox("all")))
        self._gdwh_canvas.bind(
            "<Configure>",
            lambda e: self._gdwh_canvas.itemconfig(win_id, width=e.width))

        self._build_gdwh_step1(self._gdwh_sf)
        self._build_gdwh_step2(self._gdwh_sf)
        self._build_gdwh_step3(self._gdwh_sf)
        self._build_gdwh_step4(self._gdwh_sf)

    def _build_gdwh_step1(self, parent):
        sec = ttk.LabelFrame(
            parent,
            text="1   Umgebung",
            padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(3, weight=1)

        ttk.Label(sec, text="Umgebung:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._gdwh_env_var = tk.StringVar(value="INT")
        self._gdwh_env_radios: List[ttk.Radiobutton] = []
        for col, env in enumerate(("INT", "PROD"), 1):
            rb = ttk.Radiobutton(sec, text=env, variable=self._gdwh_env_var, value=env,
                                  command=self._gdwh_on_env_change)
            rb.grid(row=0, column=col, sticky="w", padx=4)
            self._gdwh_env_radios.append(rb)

        self._gdwh_url_lbl = ttk.Label(sec, text=GDWH_ENVIRONMENTS["INT"],
                                        font=("Segoe UI", 8), style="Dim.TLabel")
        self._gdwh_url_lbl.grid(row=0, column=3, sticky="w", padx=12)

        ttk.Label(
            sec,
            text="Authentifizierung: Windows-Session (aktuell eingeloggter User, wie im Browser)",
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

    def _build_gdwh_step2(self, parent):
        sec = ttk.LabelFrame(parent, text="2   GDS-Key & Imports laden",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(1, weight=1)

        ttk.Label(sec, text="Auftragstyp:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._gdwh_auftragstyp_var = tk.StringVar(value=list(AUFTRAGSTYPEN.keys())[0])
        gdwh_typ_frame = ttk.Frame(sec)
        gdwh_typ_frame.grid(row=0, column=1, columnspan=2, sticky="w")
        for typ in AUFTRAGSTYPEN:
            ttk.Radiobutton(
                gdwh_typ_frame, text=typ, variable=self._gdwh_auftragstyp_var, value=typ,
                command=self._gdwh_apply_filter,
            ).pack(side="left", padx=(0, 14))

        ttk.Label(sec, text="Jahr:").grid(row=1, column=0, sticky="w",
                                           padx=(0, 8), pady=(6, 0))
        self._gdwh_year_filter_var = tk.StringVar()
        self._gdwh_year_filter_var.trace_add("write", lambda *_: self._gdwh_apply_filter())
        ttk.Entry(sec, textvariable=self._gdwh_year_filter_var, width=8).grid(
            row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(
            sec, text="z.B. 2023  —  Leer = alle Jahre",
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=1, column=2, sticky="w", padx=(8, 0), pady=(6, 0))

        ttk.Label(sec, text="AREA:").grid(row=2, column=0, sticky="w",
                                           padx=(0, 8), pady=(6, 0))
        self._gdwh_area_filter_var = tk.StringVar()
        self._gdwh_area_filter_var.trace_add("write", lambda *_: self._gdwh_apply_filter())
        ttk.Entry(sec, textvariable=self._gdwh_area_filter_var, width=14).grid(
            row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Label(
            sec, text="z.B. OBERAAR  —  Leer = alle Areas",
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=2, column=2, sticky="w", padx=(8, 0), pady=(6, 0))

        ttk.Label(sec, text="GDS-Key:").grid(row=3, column=0, sticky="w",
                                              padx=(0, 8), pady=(6, 0))
        self._gdwh_gds_key_var = tk.StringVar(value=GDWH_GDS_KEYS[0])
        self._gdwh_gds_combo = ttk.Combobox(
            sec, textvariable=self._gdwh_gds_key_var,
            values=[GDWH_ALL_GDS_OPTION] + GDWH_GDS_KEYS, state="readonly", width=28,
        )
        self._gdwh_gds_combo.grid(row=3, column=1, sticky="w", padx=(0, 10), pady=(6, 0))
        self._gdwh_gds_combo.bind(
            "<<ComboboxSelected>>", self._gdwh_on_gds_key_change)

        self._gdwh_fetch_btn = ttk.Button(
            sec, text=self._GDWH_FETCH_BTN_LABEL,
            command=self._gdwh_fetch_imports, state="normal",
        )
        self._gdwh_fetch_btn.grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _build_gdwh_step3(self, parent):
        sec = ttk.LabelFrame(parent, text="3   DataPackages auswählen zum Löschen",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(0, weight=1)

        sel_row = ttk.Frame(sec)
        sel_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self._gdwh_sel_all_btn = ttk.Button(
            sel_row, text="Alle auswählen",
            command=self._gdwh_select_all, state="disabled",
        )
        self._gdwh_sel_all_btn.pack(side="left", padx=(0, 4))

        self._gdwh_sel_none_btn = ttk.Button(
            sel_row, text="Alle abwählen",
            command=self._gdwh_deselect_all, state="disabled",
        )
        self._gdwh_sel_none_btn.pack(side="left")

        # Umschalter Normalansicht ↔ GDWH-Anomalien (siehe _gdwh_apply_filter):
        # standardmässig werden Imports mit FileMetadata-Match sowie frisch
        # importierte, noch nicht indexierte Imports gezeigt (pending, siehe
        # _gdwh_is_pending). Nur Imports OHNE Match, die älter als
        # _GDWH_PENDING_HOURS sind, gelten als Anomalie – vermutlich bereits
        # gelöscht/nie vollständig importiert, nur noch als Historieneintrag
        # in GET /data/imports sichtbar. Erneutes "Löschen" wäre dort ein
        # No-Op mit falschem Erfolgsstatus.
        self._gdwh_show_leichen_var = tk.BooleanVar(value=False)
        self._gdwh_leichen_btn = ttk.Button(
            sel_row, text=self._GDWH_LEICHEN_BTN_LABEL,
            command=self._gdwh_toggle_leichen, state="disabled",
        )
        self._gdwh_leichen_btn.pack(side="left", padx=(12, 0))

        list_outer = tk.Frame(sec, bd=1, relief="sunken")
        list_outer.grid(row=1, column=0, sticky="nsew", pady=(0, 4))
        sec.rowconfigure(1, weight=1)

        self._gdwh_list_canvas = tk.Canvas(list_outer, height=220, highlightthickness=0)
        vsb = ttk.Scrollbar(list_outer, orient="vertical",
                             command=self._gdwh_list_canvas.yview)
        self._gdwh_list_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._gdwh_list_canvas.pack(side="left", fill="both", expand=True)

        self._gdwh_list_frame = tk.Frame(self._gdwh_list_canvas)
        lwin = self._gdwh_list_canvas.create_window(
            (0, 0), window=self._gdwh_list_frame, anchor="nw")
        self._gdwh_list_frame.bind(
            "<Configure>",
            lambda _: self._gdwh_list_canvas.configure(
                scrollregion=self._gdwh_list_canvas.bbox("all")))
        self._gdwh_list_canvas.bind(
            "<Configure>",
            lambda e: self._gdwh_list_canvas.itemconfig(lwin, width=e.width))

        # Mausrad: innerer Canvas bei Hover, äusserer GDWH-Canvas sonst
        self._gdwh_list_canvas.bind("<Enter>", lambda _: self._gdwh_list_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._gdwh_list_canvas.yview_scroll(-1 * (e.delta // 120), "units")))
        self._gdwh_list_canvas.bind("<Leave>", lambda _: self._gdwh_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._gdwh_canvas.yview_scroll(-1 * (e.delta // 120), "units")))

        self._gdwh_preview_lbl = ttk.Label(
            sec, text="Noch keine Imports geladen.",
            font=("Segoe UI", 9, "italic"), style="Dim.TLabel",
        )
        self._gdwh_preview_lbl.grid(row=2, column=0, sticky="w", pady=(2, 0))

    def _build_gdwh_step4(self, parent):
        sec = ttk.LabelFrame(parent, text="4   Löschung ausführen",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="both", expand=True, pady=(0, 4))
        sec.columnconfigure(1, weight=1)
        sec.rowconfigure(1, weight=1)

        # E-Mail (optional – GDWH schickt Benachrichtigung nach Abschluss)
        ttk.Label(sec, text="E-Mail (optional):").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        self._gdwh_email_var = tk.StringVar()
        ttk.Entry(sec, textvariable=self._gdwh_email_var, width=40).grid(
            row=0, column=1, sticky="w", pady=(0, 8))
        ttk.Label(
            sec,
            text="GDWH schickt Benachrichtigung wenn der Lösch-Job abgeschlossen ist",
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=0, column=2, sticky="w", padx=(10, 0), pady=(0, 8))

        self._gdwh_log = scrolledtext.ScrolledText(
            sec, height=10, state="disabled",
            font=("Cascadia Mono", 9), wrap="word",
        )
        self._gdwh_log.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(0, 8))

        btn_row = ttk.Frame(sec)
        btn_row.grid(row=2, column=0, columnspan=3, sticky="ew")

        self._gdwh_del_btn = tk.Button(
            btn_row, text="Ausgewählte DataPackages löschen …",
            relief="flat", padx=14, pady=6,
            font=("Segoe UI", 10, "bold"), state="disabled",
            command=self._gdwh_start_deletion,
        )
        self._gdwh_del_btn.pack(side="left")

        self._gdwh_progress = ttk.Progressbar(btn_row, mode="determinate", length=220)
        self._gdwh_progress.pack(side="left", padx=16)

        self._gdwh_status_lbl = ttk.Label(btn_row, text="", font=("Segoe UI", 9))
        self._gdwh_status_lbl.pack(side="left")

    # ═══════════════════════════════════════════════════════════════════════════
    # Theme
    # ═══════════════════════════════════════════════════════════════════════════

    def _toggle_theme(self):
        self._apply_theme(not self._dark)

    def _apply_theme(self, dark: bool):
        self._dark = dark
        T = DARK if dark else LIGHT

        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".",
            background=T["panel"], foreground=T["fg"],
            fieldbackground=T["input"],
            selectbackground=T["sel_bg"], selectforeground=T["sel_fg"],
            bordercolor=T["sep"], lightcolor=T["panel"], darkcolor=T["sep"],
            insertcolor=T["fg"], troughcolor=T["root"],
        )
        s.configure("TFrame",     background=T["panel"])
        s.configure("TLabelframe",
            background=T["panel"], bordercolor=T["sep"])
        s.configure("TLabelframe.Label",
            background=T["panel"], foreground=T["fg"],
            font=("Segoe UI", 9, "bold"))
        s.configure("Section.TLabelframe",
            background=T["panel"], bordercolor=T["sep"])
        s.configure("Section.TLabelframe.Label",
            background=T["panel"], foreground=T["accent"],
            font=("Segoe UI", 10, "bold"))
        s.configure("TLabel",     background=T["panel"], foreground=T["fg"])
        s.configure("Dim.TLabel", background=T["panel"], foreground=T["fg_dim"])
        s.configure("TButton",
            background=T["btn"], foreground=T["fg"],
            bordercolor=T["sep"], relief="flat",
            padding=(8, 4), focuscolor=T["panel"],
        )
        # WICHTIG: s.map() ERSETZT die komplette State-Map einer Option, statt
        # sie mit der Theme-Vorgabe zu kombinieren – ohne einen expliziten
        # "disabled"-Eintrag hier geht die eingebaute Clam-Abdunklung für
        # deaktivierte Buttons verloren. Sie sähen dann optisch identisch zu
        # aktiven Buttons aus (nur ohne Hover-Reaktion beim Drüberfahren),
        # was wie ein Darstellungsfehler wirkt statt wie "gerade nicht klickbar".
        s.map("TButton",
            background=[("disabled", T["btn"]), ("active", T["btn_hover"]),
                       ("pressed", T["sep"])],
            foreground=[("disabled", T["fg_dim"]), ("active", T["fg"])],
            relief=[("pressed", "flat")],
        )
        s.configure("Amber.TButton",
            background=T["btn"], foreground=T["hint"],
            bordercolor=T["sep"], relief="flat",
            padding=(8, 4), focuscolor=T["panel"],
        )
        s.map("Amber.TButton",
            background=[("disabled", T["btn"]), ("active", T["btn_hover"]),
                       ("pressed", T["sep"])],
            foreground=[("disabled", T["fg_dim"]), ("active", T["hint"])],
            relief=[("pressed", "flat")],
        )
        s.configure("AmberBold.TButton",
            background=T["btn"], foreground=T["hint"],
            bordercolor=T["sep"], relief="flat",
            padding=(8, 4), focuscolor=T["panel"],
            font=("Segoe UI", 10, "bold"),
        )
        s.map("AmberBold.TButton",
            background=[("disabled", T["btn"]), ("active", T["btn_hover"]),
                       ("pressed", T["sep"])],
            foreground=[("disabled", T["fg_dim"]), ("active", T["hint"])],
            relief=[("pressed", "flat")],
        )
        s.configure("Green.TButton",
            background=T["btn"], foreground=T["ok"],
            bordercolor=T["sep"], relief="flat",
            padding=(8, 4), focuscolor=T["panel"],
        )
        s.map("Green.TButton",
            background=[("disabled", T["btn"]), ("active", T["btn_hover"]),
                       ("pressed", T["sep"])],
            foreground=[("disabled", T["fg_dim"]), ("active", T["ok"])],
            relief=[("pressed", "flat")],
        )
        s.configure("TRadiobutton",
            background=T["panel"], foreground=T["fg"], focuscolor=T["panel"])
        s.map("TRadiobutton",
            background=[("active", T["panel"])], foreground=[("active", T["fg"])])
        s.configure("TCheckbutton",
            background=T["panel"], foreground=T["fg"], focuscolor=T["panel"])
        s.map("TCheckbutton",
            background=[("active", T["panel"])], foreground=[("active", T["fg"])])
        s.configure("TEntry",
            fieldbackground=T["input"], foreground=T["fg"],
            bordercolor=T["sep"], insertcolor=T["fg"],
            selectbackground=T["sel_bg"], selectforeground=T["sel_fg"],
        )
        s.configure("TCombobox",
            fieldbackground=T["input"], foreground=T["fg"],
            background=T["input"], bordercolor=T["sep"], arrowcolor=T["fg"],
            selectbackground=T["sel_bg"], selectforeground=T["sel_fg"],
        )
        # "readonly" (State der GDS-Key-Auswahl) übersteuert sonst die obige
        # fieldbackground mit der hellen Theme-Vorgabe – muss pro State
        # gemappt werden, sonst bleibt das Feld im Dark Mode zu hell.
        s.map("TCombobox",
            fieldbackground=[("readonly", T["input"]), ("disabled", T["input"])],
            foreground=[("readonly", T["fg"])],
            selectbackground=[("readonly", T["sel_bg"])],
            selectforeground=[("readonly", T["sel_fg"])],
        )
        self.option_add("*TCombobox*Listbox.background", T["input"])
        self.option_add("*TCombobox*Listbox.foreground", T["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", T["sel_bg"])
        self.option_add("*TCombobox*Listbox.selectForeground", T["sel_fg"])
        s.configure("Vertical.TScrollbar",
            background=T["btn"], troughcolor=T["root"],
            bordercolor=T["sep"], arrowcolor=T["fg"],
        )
        s.configure("Horizontal.TScrollbar",
            background=T["btn"], troughcolor=T["root"],
            bordercolor=T["sep"], arrowcolor=T["fg"],
        )
        s.configure("Treeview",
            background=T["list"], foreground=T["fg"],
            fieldbackground=T["list"], rowheight=22, bordercolor=T["sep"])
        s.configure("Treeview.Heading",
            background=T["btn"], foreground=T["fg"],
            relief="flat", padding=(4, 4))
        s.map("Treeview",
            background=[("selected", T["sel_bg"])],
            foreground=[("selected", T["sel_fg"])])
        s.map("Treeview.Heading",
            background=[("active", T["btn_hover"])])
        s.configure("TSeparator", background=T["sep"])
        s.configure("TProgressbar",
            background=T["accent"], troughcolor=T["root"], bordercolor=T["sep"])
        s.configure("TNotebook",
            background=T["root"], bordercolor=T["sep"],
            tabmargins=[2, 4, 0, 0])
        s.configure("TNotebook.Tab",
            background=T["btn"], foreground=T["fg_dim"],
            padding=[10, 3],          # klein: nicht-aktiver Tab
            focuscolor=T["panel"])
        s.map("TNotebook.Tab",
            background=[("selected", T["sel_bg"]), ("active", T["btn_hover"])],
            foreground=[("selected", T["sel_fg"]),  ("active", T["fg"])],
            padding=[("selected", [16, 7])],   # grösser: aktiver Tab
        )

        self.configure(bg=T["root"])
        self._canvas.configure(bg=T["panel"], highlightbackground=T["sep"])
        self._gdwh_canvas.configure(bg=T["panel"], highlightbackground=T["sep"])
        self._hdr.configure(bg=T["hdr_bg"])
        self._hdr_lbl.configure(bg=T["hdr_bg"], fg=T["hdr_fg"])
        self._theme_btn.configure(
            bg=T["hdr_bg"], fg=T["hdr_fg"],
            activebackground=T["btn"], activeforeground=T["fg"],
            text="Hell" if dark else "Dark",
        )
        self._log.configure(bg=T["log_bg"], fg=T["log_fg"],
                             insertbackground=T["log_fg"])
        self._gdwh_log.configure(bg=T["log_bg"], fg=T["log_fg"],
                                  insertbackground=T["log_fg"])

        # STAC Asset-Tree
        self._tree.tag_configure("item", foreground=T["chk_item"], font=("Segoe UI", 9, "bold"))
        self._tree.tag_configure("item_selected", foreground=T["hint"], font=("Segoe UI", 9, "bold"))
        self._tree.tag_configure("item_empty", foreground=T["err"], font=("Segoe UI", 9, "italic"))
        self._tree.tag_configure("item_empty_selected", foreground=T["hint"], font=("Segoe UI", 9, "bold"))
        self._tree.tag_configure("asset_ok",   foreground=T["ok"])
        self._tree.tag_configure("asset_err",  foreground=T["err"])
        self._tree.tag_configure("asset_warn", foreground=T["hint"])
        self._tree.tag_configure("asset_dim",  foreground=T["fg_dim"])
        # Amber-Auswahlmarkierung: gilt nur, solange kein echtes Prüfergebnis
        # (asset_ok/err/warn) vorliegt – siehe _effective_asset_tag().
        self._tree.tag_configure("asset_selected", foreground=T["hint"])
        # Zusatz-Tag (kombiniert mit obigen, setzt nur die Schrift): macht eine
        # Auswahl auch dann sichtbar, wenn die Zeile bereits eine Statusfarbe
        # trägt (rot/orange) und die Glyphe ◯→⬤ allein zu unauffällig wäre.
        self._tree.tag_configure("checked_asset", font=("Segoe UI", 9, "bold"))

        self._gdwh_list_canvas.configure(bg=T["chk_bg"])
        self._gdwh_list_frame.configure(bg=T["chk_bg"])
        self._gdwh_recolor_list(T)

        # STAC Lösch-Button
        if str(self._del_btn["state"]) == "disabled":
            self._del_btn.configure(
                bg=T["btn"], fg=T["fg_dim"],
                activebackground=T["btn_hover"], activeforeground=T["fg"])
        else:
            self._del_btn.configure(
                bg=T["err"], fg="#ffffff",
                activebackground="#b71c1c" if dark else "#c62828",
                activeforeground="#ffffff")

        # GDWH Lösch-Button
        if str(self._gdwh_del_btn["state"]) == "disabled":
            self._gdwh_del_btn.configure(
                bg=T["btn"], fg=T["fg_dim"],
                activebackground=T["btn_hover"], activeforeground=T["fg"])
        else:
            self._gdwh_del_btn.configure(
                bg=T["err"], fg="#ffffff",
                activebackground="#b71c1c" if dark else "#c62828",
                activeforeground="#ffffff")

        self._set_titlebar_dark(dark)

    def _gdwh_recolor_list(self, T: dict):
        def recolor(widget):
            cls = widget.winfo_class()
            if cls == "Frame":
                widget.configure(bg=T["chk_bg"])
            elif cls == "Label":
                if getattr(widget, "_is_pkg_header", False):
                    widget.configure(bg=T["chk_bg"], fg=T["chk_item"])
                else:
                    widget.configure(bg=T["chk_bg"], fg=T["fg_dim"])
            elif cls == "Checkbutton":
                widget.configure(
                    bg=T["chk_bg"], fg=T["fg"],
                    selectcolor=T["input"],
                    activebackground=T["chk_bg"], activeforeground=T["fg"])
            for child in widget.winfo_children():
                recolor(child)
        recolor(self._gdwh_list_frame)

    def _set_titlebar_dark(self, dark: bool):
        if not self.winfo_ismapped():
            self.after(50, lambda: self._set_titlebar_dark(dark))
            return
        try:
            hwnd  = int(self.wm_frame(), 16)
            value = ctypes.c_int(1 if dark else 0)
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    break
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════════════════
    # STAC – Event Handler
    # ═══════════════════════════════════════════════════════════════════════════

    def _on_env_change(self):
        # Sicherheitskritisch: eine bereits geladene Item/Asset-Liste stammt aus
        # der VORHER gewählten Umgebung. Sie muss beim Umgebungswechsel sofort
        # verworfen werden – sonst könnte eine noch angehakte Auswahl aus INT
        # nach dem Wechsel auf PROD (oder umgekehrt) gelöscht werden, weil
        # Löschungen stets gegen die AKTUELL gewählte Umgebung laufen.
        env = self._env_var.get()
        self._url_lbl.configure(text=ENVIRONMENTS[env])
        self._auth = None
        self._cred_status.configure(text="nicht geladen")
        self._cred_btn.configure(style="Amber.TButton")
        self._load_btn.config(state="disabled")
        self._load_btn.configure(style="AmberBold.TButton")
        # Neue Umgebung = noch nichts geladen: Button-Text auf Ausgangstext
        # zurücksetzen, sonst würde fälschlich "aktualisieren" stehen.
        self._items_loaded_once = False
        self._load_btn.config(text=self._LOAD_BTN_LABEL)
        self._clear_tree()
        self._clear_state()
        self._preview_lbl.configure(text="Noch keine Vorschau geladen.")
        self._del_btn.config(text="Ausgewählte Assets löschen …", state="disabled")
        self._apply_theme(self._dark)

    def _on_auftragstyp_change(self):
        # Auftragstyp ist ein reiner Client-Filter auf der bereits geladenen
        # Liste – er darf das Item-ID-Suchfeld (Server-Filter beim Laden)
        # nicht mehr überschreiben, sonst werden andere Auftragstypen nie
        # geladen und bleiben nach dem Wechsel leer.
        self._update_no_thumb_btn_visibility()
        self._apply_filters()

    def _update_no_thumb_btn_visibility(self):
        """'ITEMs ohne Thumbnail' ergibt nur bei RAM (Thumbnail-Pflicht) Sinn –
        Button nur dort einblenden. Beim Wegschalten von RAM wird ein aktiver
        Filter automatisch zurückgesetzt (sonst bliebe die Auswahl unsichtbar
        aktiv hängen)."""
        # winfo_manager() statt winfo_ismapped(): Letzteres hängt zusätzlich
        # davon ab, ob das Fenster gerade tatsächlich auf dem Bildschirm
        # sichtbar ist (z.B. False direkt nach dem Bauen, vor dem ersten
        # Map-Event) – winfo_manager() spiegelt zuverlässig nur den reinen
        # Pack-Zustand des Widgets.
        is_ram = AUFTRAGSTYPEN.get(self._auftragstyp_var.get(), "") == "ram"
        if is_ram:
            if self._show_no_thumb_btn.winfo_manager() != "pack":
                self._show_no_thumb_btn.pack(side="left")
        else:
            if self._show_no_thumb_btn.winfo_manager() == "pack":
                self._show_no_thumb_btn.pack_forget()
            if self._show_no_thumb_only:
                self._show_no_thumb_only = False
                self._show_no_thumb_btn.config(text=self._SHOW_NO_THUMB_BTN_LABEL)

    def _load_credentials(self):
        env = self._env_var.get()
        try:
            cfg_path = Path(__file__).parent / "secrets" / "stac_credentials.json"
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
            env_cfg  = cfg[env]
            username = env_cfg["username"]
            password = env_cfg["password"]
            self._auth     = (username, password)
            self._base_url = ENVIRONMENTS[env]
            T = DARK if self._dark else LIGHT
            self._cred_status.configure(text=f"Geladen: {username}", foreground=T["ok"])
            self._cred_btn.configure(style="TButton")
            self._load_btn.config(state="normal")
            self._load_btn.configure(style="AmberBold.TButton")
            self._log_write(f"[STAC Credentials] {env} – Benutzer: {username}\n")
        except Exception as exc:
            T = DARK if self._dark else LIGHT
            self._cred_status.configure(text="Fehler!", foreground=T["err"])
            self._cred_btn.configure(style="Amber.TButton")
            messagebox.showerror("Credentials-Fehler", str(exc))

    def _load(self):
        self._load_btn.configure(style="TButton")
        self._set_busy(True)
        self._del_btn.config(state="disabled")
        self._apply_theme(self._dark)
        self._clear_tree()
        self._preview_lbl.configure(text="Lade …")
        self._clear_state()
        threading.Thread(target=self._load_worker, daemon=True).start()

    # ── STAC Worker-Thread ────────────────────────────────────────────────────

    def _load_worker(self):
        # "Item-Liste laden" holt IMMER die komplette Collection. Auftragstyp,
        # Jahr, Item-ID, Asset-Key und Dateiendung sind reine Client-Filter,
        # die erst danach in _apply_filters() auf diese vollständige Liste
        # angewendet werden – so filtern Änderungen an diesen Feldern sofort
        # die bereits geladene Liste, statt eine neue (Teil-)Ladung zu verlangen.
        try:
            self._log_write("[Abruf] Hole alle Items der Collection …\n")
            all_items = get_collection_items(self._base_url, self._auth, self._log_write)
            self._log_write(f"[Abruf] {len(all_items)} Items total.\n")
            if not all_items:
                self.after(0, lambda: self._preview_lbl.configure(
                    text="Keine Items gefunden."))
                return

            hrefs_map: Dict[str, Dict[str, str]] = {}
            full_items: List[Dict] = []
            for i, item in enumerate(all_items, 1):
                iid    = item["id"]
                assets = item.get("assets", {})
                if not assets:
                    full = get_item_direct(self._base_url, self._auth, iid)
                    if full is not None:
                        item   = full
                        assets = item.get("assets", {})
                hrefs_map[iid] = {k: v.get("href", "") for k, v in assets.items()}
                full_items.append(item)
                self._log_write(
                    f"  [{i}/{len(all_items)}] {iid}: {len(hrefs_map[iid])} Asset(s)\n")
            self._items_preview     = full_items
            self._items_asset_hrefs = hrefs_map
            self.after(0, self._apply_filters)
        except Exception as exc:
            self._log_write(f"[FEHLER] {exc}\n")
            self.after(0, lambda: messagebox.showerror("Fehler", str(exc)))
        finally:
            self.after(0, lambda: self._set_busy(False))

    # ── STAC Filterung ────────────────────────────────────────────────────────

    def _get_active_extensions(self) -> List[str]:
        result = []
        for var, exts in self._ext_vars:
            if var.get():
                result.extend(exts)
        for part in self._ext_custom_var.get().replace(",", " ").split():
            result.append(part if part.startswith(".") else f".{part}")
        return result

    def _item_has_thumbnail(self, iid: str) -> bool:
        """Prüft anhand der ROHEN (ungefilterten) Assets, ob ein Item ein
        thumbnail.jpg besitzt – unabhängig von Asset-Key-/Extension-Filtern."""
        for key, href in self._items_asset_hrefs.get(iid, {}).items():
            key_l = key.lower()
            if "thumbnail" in key_l or (href and href.lower().endswith("thumbnail.jpg")):
                return True
        return False

    def _no_thumb_excluded(self, iid: str) -> bool:
        """True, wenn Item-ID oder eines seiner Asset-Keys
        _NO_THUMB_EXCLUDE_SUBSTR enthält – solche Items haben planmässig nie
        ein Thumbnail und sollen im 'ITEMs ohne Thumbnail'-Filter deshalb
        NICHT als (vermeintliche) Kandidaten auftauchen."""
        needle = self._NO_THUMB_EXCLUDE_SUBSTR
        if needle in iid.lower():
            return True
        return any(needle in ak.lower()
                   for ak in self._items_asset_hrefs.get(iid, {}))

    def _apply_filters(self):
        if not self._items_asset_hrefs:
            return
        id_filter   = self._item_id_var.get().strip().lower()
        typ_filter  = AUFTRAGSTYPEN.get(self._auftragstyp_var.get(), "").strip().lower()
        year_filter = self._year_filter_var.get().strip()
        area_filter = self._area_filter_var.get().strip()
        key_filter  = self._asset_filter_var.get().strip().lower()
        extensions  = self._get_active_extensions()
        assets_map: Dict[str, List[str]] = {}
        for iid, key_href in self._items_asset_hrefs.items():
            keys = []
            for k, href in key_href.items():
                if key_filter and key_filter not in k.lower():
                    continue
                if extensions:
                    href_l, k_l = href.lower(), k.lower()
                    if not any(href_l.endswith(e) or k_l.endswith(e) for e in extensions):
                        continue
                if self._show_faulty_only:
                    tag = self._asset_status.get((iid, k), {}).get("tag", "asset_dim")
                    if tag not in ("asset_err", "asset_warn"):
                        continue
                keys.append(k)
            assets_map[iid] = keys
        self._items_assets = assets_map
        items = self._items_preview
        if id_filter:
            items = [it for it in items if id_filter in it["id"].lower()]
        if typ_filter:
            items = [it for it in items if typ_filter in it["id"].lower()]
        if year_filter:
            items = [it for it in items if stac_item_year(it) == year_filter]
        if area_filter:
            items = [it for it in items
                     if area_filter.lower() in stac_item_area(it).lower()]
        if self._show_no_thumb_only:
            items = [it for it in items
                     if not self._item_has_thumbnail(it["id"])
                     and not self._no_thumb_excluded(it["id"])]
        self._populate_tree(items, assets_map)

    # ── STAC Asset-Tree ───────────────────────────────────────────────────────

    def _clear_tree(self):
        self._tree.delete(*self._tree.get_children())
        self._nodes.clear()
        self._checked.clear()
        self._checked_items.clear()

    def _is_checked(self, asset_nid: str) -> bool:
        return self._checked.get(asset_nid, False)

    def _chk_glyph(self, asset_nid: str) -> str:
        return self._CHK_ON if self._is_checked(asset_nid) else self._CHK_OFF

    def _effective_asset_tag(self, asset_nid: str) -> str:
        """Zeilen-Tag für eine Asset-Zeile: ein echtes Prüfergebnis
        (asset_ok/err/warn) hat immer Vorrang vor der reinen
        Amber-Auswahlmarkierung – sonst ginge die Information verloren,
        ob ein ausgewähltes Asset z.B. fehlgeschlagen ist."""
        status_tag = self._nodes.get(asset_nid, {}).get("status_tag", "asset_dim")
        if status_tag != "asset_dim":
            return status_tag
        return "asset_selected" if self._is_checked(asset_nid) else "asset_dim"

    def _asset_row_tags(self, asset_nid: str) -> Tuple[str, ...]:
        """Tag-Liste für eine Asset-Zeile. Bei einem Prüfergebnis (rot/orange)
        überdeckt dessen Farbe die Amber-Auswahlmarkierung (siehe
        _effective_asset_tag) – ohne weiteres Signal wäre eine Auswahl auf
        einer bereits fehlerhaft eingefärbten Zeile optisch nicht erkennbar
        (nur die kleine Kreis-Glyphe ändert sich). Deshalb zusätzlich fett,
        wenn ausgewählt – unabhängig von der Statusfarbe sichtbar."""
        tags = [self._effective_asset_tag(asset_nid)]
        if self._is_checked(asset_nid):
            tags.append("checked_asset")
        return tuple(tags)

    def _refresh_asset_row(self, asset_nid: str):
        if not self._tree.exists(asset_nid):
            return
        vals = list(self._tree.item(asset_nid, "values"))
        vals[0] = self._chk_glyph(asset_nid)
        self._tree.item(asset_nid, values=vals, tags=self._asset_row_tags(asset_nid))

    def _item_asset_nids(self, item_id: str) -> List[str]:
        return [nid for nid, d in self._nodes.items()
                if d["kind"] == "asset" and d["item_id"] == item_id]

    def _item_check_glyph(self, asset_nids: List[str]) -> str:
        if not asset_nids:
            return self._CHK_OFF
        states = [self._is_checked(n) for n in asset_nids]
        if all(states):
            return self._CHK_ON
        if not any(states):
            return self._CHK_OFF
        return self._CHK_PARTIAL

    def _refresh_item_glyph(self, item_id: str):
        item_nid = f"item::{item_id}"
        if not self._tree.exists(item_nid):
            return
        d = self._nodes.get(item_nid, {})
        if d.get("is_empty"):
            # Item ohne Assets: Checkbox hängt direkt am _checked_items-Flag,
            # nicht an Kind-Assets (es gibt keine).
            checked = self._checked_items.get(item_id, False)
            glyph = self._CHK_ON if checked else self._CHK_OFF
            tag   = "item_empty_selected" if checked else "item_empty"
        else:
            glyph = self._item_check_glyph(self._item_asset_nids(item_id))
            # Gruppenzeile wird nur bei VOLLSTÄNDIGER Auswahl amber – bei
            # Teilauswahl (◐) bleibt sie in der normalen "item"-Darstellung,
            # es gibt keinen dritten "amber-halb"-Zustand.
            tag = "item_selected" if glyph == self._CHK_ON else "item"
        vals = list(self._tree.item(item_nid, "values"))
        vals[0] = glyph
        self._tree.item(item_nid, values=vals, tags=(tag,))

    def _on_tree_click(self, event):
        if self._tree.identify_region(event.x, event.y) != "cell":
            return
        if self._tree.identify_column(event.x) != "#1":  # "sel"-Spalte
            return
        row = self._tree.identify_row(event.y)
        d = self._nodes.get(row)
        if not d:
            return

        if d["kind"] == "asset":
            self._checked[row] = not self._is_checked(row)
            self._refresh_asset_row(row)
            self._refresh_item_glyph(d["item_id"])
        elif d.get("is_empty"):  # Item ohne Assets: eigene Checkbox
            iid = d["item_id"]
            self._checked_items[iid] = not self._checked_items.get(iid, False)
            self._refresh_item_glyph(iid)
        else:  # item: alle zugehörigen Assets gemeinsam (de)selektieren
            asset_nids = self._item_asset_nids(d["item_id"])
            new_state  = self._item_check_glyph(asset_nids) != self._CHK_ON
            for nid in asset_nids:
                self._checked[nid] = new_state
                self._refresh_asset_row(nid)
            self._refresh_item_glyph(d["item_id"])
        self._update_preview_label()
        return "break"

    def _populate_tree(self, items: List[Dict],
                        assets_map: Dict[str, List[str]]):
        self._clear_tree()
        any_visible = False

        # Alle gefilterten Items anzeigen, auch OHNE Assets – solche leeren
        # Items sollen im Baum auffindbar und direkt löschbar sein, statt
        # unsichtbar zu verschwinden. Sortiert nach Aufnahmedatum (neueste zuerst).
        visible = list(items)
        visible.sort(key=stac_item_acq_date, reverse=True)
        _pfx = COLLECTION_ID + "_"

        for item in visible:
            iid        = item["id"]
            asset_keys = assets_map.get(iid, [])
            # "leer" bedeutet: das Item hat ÜBERHAUPT keine Assets (roh, vor
            # Key/Extension/Fault-Filter) – nicht bloss, dass unter dem
            # aktuellen Filter kein Asset matcht. Nur echte Leer-Items
            # bekommen die Item-Lösch-Checkbox; Items, deren Assets nur der
            # Filter ausblendet, werden ganz übersprungen (wie zuvor).
            is_empty = not self._items_asset_hrefs.get(iid)
            if not asset_keys and not is_empty:
                continue
            any_visible = True

            area = stac_item_area(item)
            acq  = stac_item_acq_date(item)
            display = iid[len(_pfx):] if iid.startswith(_pfx) else iid
            meta  = "  ".join(p for p in [area, acq] if p)
            label = display + (f"   [{meta}]" if meta else "")

            asset_node_ids = [f"asset::{iid}::{ak}" for ak in asset_keys]
            node_id = f"item::{iid}"
            self._nodes[node_id] = {
                "kind": "item", "item_id": iid, "item": item, "is_empty": is_empty}
            if is_empty:
                checked = self._checked_items.get(iid, False)
                glyph = self._CHK_ON if checked else self._CHK_OFF
                tag   = "item_empty_selected" if checked else "item_empty"
                groesse_txt = "0 Assets (leer)"
            else:
                glyph = self._item_check_glyph(asset_node_ids)
                tag   = "item_selected" if glyph == self._CHK_ON else "item"
                groesse_txt = f"{len(asset_keys)} Assets"
            self._tree.insert("", "end", iid=node_id, text=f"  {label}",
                              values=(glyph, area, "", "", groesse_txt, ""),
                              tags=(tag,), open=True)

            assets_dict = item.get("assets", {})
            for ak in asset_keys:
                aval   = assets_dict.get(ak, {})
                href   = self._items_asset_hrefs.get(iid, {}).get(ak, "")
                a_area = asset_area(aval) or area
                ext    = Path(href).suffix if href else ""
                atype  = aval.get("type", "")

                anid = f"asset::{iid}::{ak}"
                info = self._asset_status.get((iid, ak), {})
                status_tag = info.get("tag", "asset_dim")
                self._tree.insert(node_id, "end", iid=anid, text=f"        {ak}",
                                  values=(self._chk_glyph(anid), a_area,
                                          info.get("status_text", ""),
                                          ext or atype[:22],
                                          info.get("size_text", ""),
                                          info.get("date_text", "")),
                                  tags=(status_tag,))
                self._nodes[anid] = {
                    "kind": "asset", "item_id": iid, "asset_key": ak,
                    "href": href, "item": item, "status_tag": status_tag,
                }

        if not any_visible:
            if self._show_faulty_only and self._show_no_thumb_only:
                text = "Keine fehlerhaften/leeren Items ohne Thumbnail nach aktuellem Filter."
            elif self._show_faulty_only:
                text = "Keine fehlerhaften Assets/leeren Items nach aktuellem Filter."
            elif self._show_no_thumb_only:
                text = "Keine Items ohne Thumbnail nach aktuellem Filter."
            else:
                text = "Keine Assets nach aktuellem Filter."
            self._preview_lbl.configure(text=text)
        else:
            self._items_loaded_once = True

        self._enable_search_btns()
        st = "normal" if any_visible else "disabled"
        self._sel_all_btn.config(state=st)
        self._sel_none_btn.config(state=st)
        self._check_btn.config(state=st)
        self._expand_btn.config(state=st)
        self._collapse_btn.config(state=st)
        # Leere Items gelten als "fehlerhaft" (siehe _select_faulty_assets/
        # "Fehlerhafte anzeigen") und sind ohne HEAD-Prüfung bereits
        # bekannt – Button also auch ohne Fehlertreffer aus "Assets prüfen"
        # freischalten, sobald es welche gibt (sonst bliebe er bei einer
        # reinen Leer-Item-Situation dauerhaft grau).
        self._sel_faulty_btn.config(
            state="normal" if self._has_faulty_or_empty() else "disabled")
        # Unabhängig von der aktuellen Filter-Trefferzahl klickbar halten,
        # solange überhaupt Daten geladen sind – sonst könnten sich diese
        # Buttons im aktiven Toggle-Zustand selbst aussperren, falls der
        # gefilterte Blick gerade leer ist (z.B. keine Fehler gefunden).
        has_data = bool(self._items_preview)
        self._show_faulty_btn.config(state="normal" if has_data else "disabled")
        self._show_no_thumb_btn.config(state="normal" if has_data else "disabled")
        self._update_preview_label()
        self._apply_theme(self._dark)

    def _has_faulty_or_empty(self) -> bool:
        """True, sobald es unter den GELADENEN (nicht nur sichtbaren) Daten
        entweder ein Asset mit Fehlerstatus (aus 'Assets prüfen') oder ein
        Item ganz ohne Assets gibt."""
        has_faulty = any(info.get("tag") in ("asset_err", "asset_warn")
                         for info in self._asset_status.values())
        has_empty  = any(not hrefs for hrefs in self._items_asset_hrefs.values())
        return has_faulty or has_empty

    def _expand_all(self):
        for node in self._tree.get_children():
            self._tree.item(node, open=True)

    def _collapse_all(self):
        for node in self._tree.get_children():
            self._tree.item(node, open=False)

    # ── STAC Asset-Prüfung ────────────────────────────────────────────────────

    def _check_assets(self):
        tasks = [
            (nid, d["item_id"], d["asset_key"], d["href"])
            for nid, d in self._nodes.items()
            if d["kind"] == "asset" and d.get("href")
        ]
        if not tasks:
            self._log_write("[Prüfung] Keine Assets zum Prüfen.\n")
            return
        self._check_btn.config(state="disabled", style="Green.TButton")
        self._sel_faulty_btn.config(state="disabled")
        self._log_write(f"[Prüfung] Starte HEAD-Requests für {len(tasks)} Assets …\n")
        for nid, _, _, _ in tasks:
            if self._tree.exists(nid):
                cur = list(self._tree.item(nid, "values"))
                cur[2] = "⟳"
                # Auswahl bleibt während der Prüfung sichtbar (Amber bleibt
                # erhalten, falls Asset ausgewählt ist) – status_tag ist noch
                # "asset_dim" (kein Ergebnis), daher via effektivem Tag setzen.
                self._tree.item(nid, values=cur, tags=self._asset_row_tags(nid))
        threading.Thread(target=self._check_worker, args=(tasks,), daemon=True).start()

    def _check_worker(self, tasks: List[Tuple[str, str, str, str]]):
        errors = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            future_map = {
                pool.submit(check_asset_info, href, self._auth): (nid, iid, ak)
                for nid, iid, ak, href in tasks
            }
            for future in concurrent.futures.as_completed(future_map):
                nid, iid, ak = future_map[future]
                try:
                    info = future.result()
                except Exception:
                    info = {"status": -3, "size_bytes": None, "last_modified": None}

                sc       = info.get("status")
                sz       = info.get("size_bytes")
                lm       = info.get("last_modified")
                stxt, tg = _status_label(sc)
                if sc is None or (sc != 200 and sc != -4):
                    errors += 1

                # Cache-/CDN-Header nur ins Log, nicht in die STATUS-Spalte:
                # ein Cache-Hit (Age > 0 / X-Cache: HIT) ist bei CDN-
                # ausgelieferten Assets der Normalfall, auch bei völlig
                # gesunden 200ern – als Badge neben ✓ 200 wirkte das wie ein
                # Fehler. Für die gezielte Diagnose "laut STAC gelöscht, href
                # aber weiterhin downloadbar" reicht der Header-Dump im Log.
                cache_headers = info.get("cache_headers") or {}

                self.after(0, lambda n=nid, i=iid, a=ak, s=stxt, t=tg,
                           sz_=_fmt_size(sz), lm_=_fmt_date(lm):
                           self._update_tree_row(n, i, a, s, t, sz_, lm_))
                cache_txt = ("  |  " + ", ".join(
                    f"{k}={v}" for k, v in cache_headers.items())) if cache_headers else ""
                self._log_write(f"  {iid}/{ak}  →  {stxt}  {_fmt_size(sz)}{cache_txt}\n")

        self._log_write(f"[Prüfung] {len(tasks)} Assets geprüft — {errors} fehlerhaft.\n")
        self.after(0, lambda: self._check_btn.config(state="normal"))
        self.after(0, lambda: self._sel_faulty_btn.config(
            state="normal" if self._has_faulty_or_empty() else "disabled"))

    def _update_tree_row(self, nid: str, item_id: str, asset_key: str,
                          status_text: str, tag: str,
                          size_text: str, date_text: str):
        # Prüfergebnis unabhängig vom aktuellen Tree-Inhalt festhalten, damit
        # ein späterer Rebuild (Filterwechsel, "Nur Fehlerhafte"-Toggle) es
        # wiederverwenden kann statt es zu verlieren.
        self._asset_status[(item_id, asset_key)] = {
            "tag": tag, "status_text": status_text,
            "size_text": size_text, "date_text": date_text,
        }
        if not self._tree.exists(nid):
            return
        if nid in self._nodes:
            self._nodes[nid]["status_tag"] = tag
        cur = list(self._tree.item(nid, "values"))
        cur[2], cur[4], cur[5] = status_text, size_text, date_text
        self._tree.item(nid, values=cur, tags=self._asset_row_tags(nid))

    def _select_faulty_assets(self):
        """Wählt alle fehlerhaften Assets (Status err/warn) UND alle leeren
        Items (ganz ohne Assets) zum Löschen aus – beide gelten als
        'fehlerhaft' im Sinne dieses Buttons, analog zu 'Nur Fehlerhafte
        anzeigen'. Ersetzt dabei die bestehende Auswahl vollständig."""
        count_assets = 0
        count_items = 0
        for nid, d in self._nodes.items():
            if d["kind"] == "asset":
                is_error = d.get("status_tag", "asset_dim") in ("asset_err", "asset_warn")
                self._checked[nid] = is_error
                if is_error:
                    count_assets += 1
                self._refresh_asset_row(nid)
            elif d["kind"] == "item" and d.get("is_empty"):
                self._checked_items[d["item_id"]] = True
                count_items += 1
        for nid, d in self._nodes.items():
            if d["kind"] == "item":
                self._refresh_item_glyph(d["item_id"])
        self._log_write(
            f"[Auswahl] {count_assets} fehlerhafte Asset(s) + "
            f"{count_items} leere(s) Item(s) ausgewählt.\n")
        self._update_preview_label()

    def _toggle_faulty_filter(self):
        """Blendet die Tree-Ansicht auf fehlerhafte Assets (Status err/warn
        aus 'Assets prüfen') sowie leere Items ein/aus. Kombiniert sich mit
        den übrigen Filtern (Jahr/Item-ID/Asset-Key/Extension)."""
        self._show_faulty_only = not self._show_faulty_only
        self._show_faulty_btn.config(
            text=self._SHOW_ALL_BTN_LABEL if self._show_faulty_only
                 else self._SHOW_FAULTY_BTN_LABEL)
        self._apply_filters()
        if self._show_faulty_only:
            n_assets = sum(1 for d in self._nodes.values() if d["kind"] == "asset")
            n_items  = sum(1 for d in self._nodes.values()
                           if d["kind"] == "item" and d.get("is_empty"))
            self._log_write(
                f"[Filter] Nur Fehlerhafte: {n_assets} Asset(s), "
                f"{n_items} leere(s) Item(s).\n")
        else:
            self._log_write("[Filter] Zeige wieder alle Assets.\n")

    def _toggle_no_thumb_filter(self):
        """Blendet die Tree-Ansicht auf Items OHNE thumbnail.jpg-Asset ein/aus
        (nur bei Auftragstyp RAM verfügbar). Kombiniert sich mit den übrigen
        Filtern inkl. 'Fehlerhafte anzeigen'."""
        self._show_no_thumb_only = not self._show_no_thumb_only
        self._show_no_thumb_btn.config(
            text=self._SHOW_ALL_BTN_LABEL if self._show_no_thumb_only
                 else self._SHOW_NO_THUMB_BTN_LABEL)
        self._apply_filters()
        if self._show_no_thumb_only:
            n_items = sum(1 for d in self._nodes.values() if d["kind"] == "item")
            self._log_write(f"[Filter] Items ohne Thumbnail: {n_items} Item(s).\n")
        else:
            self._log_write("[Filter] Zeige wieder alle Assets.\n")

    def _select_all_assets(self):
        self._set_all_checked(True)

    def _deselect_all_assets(self):
        self._set_all_checked(False)

    def _set_all_checked(self, state: bool):
        for nid, d in self._nodes.items():
            if d["kind"] == "asset":
                self._checked[nid] = state
                self._refresh_asset_row(nid)
            elif d["kind"] == "item" and d.get("is_empty"):
                self._checked_items[d["item_id"]] = state
        for nid, d in self._nodes.items():
            if d["kind"] == "item":
                self._refresh_item_glyph(d["item_id"])
        self._update_preview_label()

    def _update_preview_label(self):
        asset_nids = [nid for nid, d in self._nodes.items() if d["kind"] == "asset"]
        total          = len(asset_nids)
        selected       = sum(1 for nid in asset_nids if self._is_checked(nid))
        empty_selected = sum(1 for v in self._checked_items.values() if v)
        n_total    = sum(len(v) for v in self._items_asset_hrefs.values())
        extra = f"  |  {empty_selected} leere(s) Item(s) ausgewählt" if empty_selected else ""
        self._preview_lbl.configure(
            text=f"{len(self._items_preview)} Item(s)  |  "
                 f"{n_total} Assets total  →  {total} nach Filter  |  "
                 f"{selected} Asset(s) ausgewählt zum Löschen{extra}"
        )
        if empty_selected:
            btn_text = f"Auswahl löschen ({selected} Asset(s), {empty_selected} leere(s) Item(s))"
        else:
            btn_text = f"Asset Auswahl ({selected}) löschen"
        self._del_btn.config(
            text=btn_text,
            state="normal" if (selected + empty_selected) > 0 else "disabled",
        )
        self._apply_theme(self._dark)

    # ── STAC Kontextmenü / Doppelklick ────────────────────────────────────────

    def _open_stac_browser(self, item_id: Optional[str] = None):
        url = browser_url(self._env_var.get(), item_id)
        webbrowser.open(url)
        self.clipboard_clear()
        self.clipboard_append(url)
        self._log_write(f"[STAC Browser] geöffnet & kopiert: {url}\n")

    def _on_right_click(self, event):
        row = self._tree.identify_row(event.y)
        if not row:
            return
        self._tree.selection_set(row)
        d = self._nodes.get(row, {})
        self._ctx.delete(0, "end")

        if d.get("kind") == "asset":
            href = d.get("href", "")
            if href:
                self._ctx.add_command(
                    label="URL kopieren", command=lambda h=href: self._clip(h))
                self._ctx.add_command(
                    label="Im Browser öffnen", command=lambda h=href: webbrowser.open(h))
                self._ctx.add_separator()

        iid = d.get("item_id")
        if iid:
            self._ctx.add_command(
                label="Item-ID kopieren", command=lambda i=iid: self._clip(i))
            self._ctx.add_command(
                label="Im STAC Browser öffnen",
                command=lambda i=iid: self._open_stac_browser(i))

        self._ctx.tk_popup(event.x_root, event.y_root)

    def _on_double_click(self, event):
        row = self._tree.identify_row(event.y)
        if not row:
            return
        d = self._nodes.get(row, {})
        if d.get("kind") == "asset":
            href = d.get("href", "")
            if href:
                webbrowser.open(href)

    def _clip(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log_write(f"[Clipboard] {text}\n")

    # ── STAC Löschung ─────────────────────────────────────────────────────────

    def _start_deletion(self):
        selected_items: Dict[str, List[str]] = {}
        for nid, d in self._nodes.items():
            if d["kind"] == "asset" and self._is_checked(nid):
                selected_items.setdefault(d["item_id"], []).append(d["asset_key"])
        empty_items_to_delete = [iid for iid, v in self._checked_items.items() if v]
        if not selected_items and not empty_items_to_delete:
            messagebox.showwarning("Nichts ausgewählt", "Keine Assets oder Items ausgewählt.")
            return
        total_assets = sum(len(v) for v in selected_items.values())
        items_fully_deleted = sum(
            1 for iid, asset_keys in selected_items.items()
            if len(asset_keys) == len(self._items_asset_hrefs.get(iid, {}))
        ) + len(empty_items_to_delete)
        total_items_affected = len(selected_items) + len(empty_items_to_delete)
        dlg = ConfirmDialog(self, self._env_var.get(), total_items_affected,
                            total_assets, items_fully_deleted, self._dark)
        if not dlg.result:
            self._log_write("[Abbruch] Löschung durch Benutzer abgebrochen.\n")
            return
        self._del_btn.config(state="disabled")
        self._disable_search_btns()
        self._set_env_controls_locked(True)
        self._apply_theme(self._dark)
        self._progress["maximum"] = total_assets + len(empty_items_to_delete)
        self._progress["value"]   = 0
        self._status_lbl.configure(text="Lösche …")
        # base_url/auth JETZT einfrieren: die Löschung läuft in einem Hintergrund-
        # Thread über potenziell viele Requests. Würde man self._base_url/self._auth
        # live im Loop lesen, könnte ein Umgebungswechsel MITTEN in der laufenden
        # Löschung (Radiobutton + "Credentials laden" sind sonst weiter klickbar)
        # spätere Assets gegen die NEUE Umgebung löschen, obwohl deren IDs aus der
        # ALTEN Umgebung stammen. Deshalb: einmal snapshotten, nur noch lokal nutzen.
        base_url = self._base_url
        auth     = self._auth
        threading.Thread(target=self._delete_worker,
                         args=(selected_items, empty_items_to_delete, base_url, auth),
                         daemon=True).start()

    def _recover_stuck_upload(self, base_url: str, auth: Tuple, iid: str, ak: str,
                               session_logger: logging.Logger, env: str) -> Tuple[bool, int, str]:
        """Rettet einen an 'has still an upload in progress' gescheiterten
        Asset-Löschversuch: listet offene Multipart-Uploads des Assets (STAC
        Upload-Extension), bricht sie ab und versucht die Löschung danach
        einmal erneut. Gibt das Ergebnis des finalen Löschversuchs zurück
        (unverändert wie ein normaler delete_asset()-Aufruf)."""
        self._log_write(f"    [Upload] offener Upload erkannt – prüfe Uploads für {ak} …\n")
        list_ok, list_code, uploads = list_asset_uploads(base_url, auth, iid, ak)
        if not list_ok:
            self._log_write(
                f"    [Upload] Liste nicht abrufbar (HTTP {list_code}): {uploads}\n")
            session_logger.warning(
                f"[STAC UPLOAD-LIST FAIL] {env}/{iid}/{ak}  HTTP {list_code}  {uploads}")
            return False, list_code, f"Upload-Liste nicht abrufbar: {uploads}"
        if not uploads:
            self._log_write(f"    [Upload] keine offenen Uploads gefunden (Status in-progress).\n")
            return False, 0, "kein offener Upload gefunden (Ursache unklar)"

        aborted_any = False
        for up in uploads:
            upload_id = up.get("upload_id") if isinstance(up, dict) else None
            status    = up.get("status", "?") if isinstance(up, dict) else "?"
            if not upload_id:
                self._log_write(f"    [Upload] unerwartetes Format: {up}\n")
                continue
            self._log_write(f"    [Upload {upload_id}] Status={status} → breche ab …\n")
            ab_ok, ab_code, ab_reason = abort_asset_upload(base_url, auth, iid, ak, upload_id)
            if ab_ok:
                aborted_any = True
                self._log_write(f"    [Upload {upload_id}] abgebrochen (HTTP {ab_code}).\n")
                session_logger.info(
                    f"[STAC UPLOAD ABORT OK] {env}/{iid}/{ak}/{upload_id}  HTTP {ab_code}")
            else:
                self._log_write(
                    f"    [Upload {upload_id}] Abbruch fehlgeschlagen "
                    f"(HTTP {ab_code}): {ab_reason}\n")
                session_logger.warning(
                    f"[STAC UPLOAD ABORT FAIL] {env}/{iid}/{ak}/{upload_id}"
                    f"  HTTP {ab_code}  {ab_reason}")

        if not aborted_any:
            return False, 0, "Upload(s) gefunden, Abbruch aber fehlgeschlagen"

        self._log_write(f"    [Retry] Lösche {ak} erneut …\n")
        return delete_asset(base_url, auth, iid, ak)

    def _delete_worker(self, selected_items: Dict[str, List[str]],
                        empty_items_to_delete: List[str],
                        base_url: str, auth: Tuple):
        ok_list        = []
        ok_pairs       = []   # [(item_id, asset_key), ...] – für Tree-/Datenbereinigung
        fail_list      = []
        items_deleted  = []
        items_del_fail = []
        done           = 0
        total          = sum(len(v) for v in selected_items.values()) + len(empty_items_to_delete)
        ts             = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        env            = self._env_var.get()

        # Metadaten aus ausgewählten Items für Log-Dateiname und Protokoll
        sel_ids  = set(selected_items) | set(empty_items_to_delete)
        sel_objs = [it for it in self._items_preview if it["id"] in sel_ids]
        _yrs = list(dict.fromkeys(stac_item_year(it)     for it in sel_objs if stac_item_year(it)))
        _ars = list(dict.fromkeys(stac_item_area(it)     for it in sel_objs if stac_item_area(it)))
        _dts = list(dict.fromkeys(stac_item_acq_date(it) for it in sel_objs if stac_item_acq_date(it)))
        meta_year    = _yrs[0] if len(_yrs) == 1 else ("multi" if _yrs else "unbekannt")
        meta_area    = _ars[0] if len(_ars) == 1 else ("multi" if _ars else "unbekannt")
        # Für Logausgabe (nicht Dateiname): alle betroffenen Areas einzeln
        # auflisten statt zu "multi" zusammenzufassen – sonst ist im Log
        # nicht mehr nachvollziehbar, welche Areas tatsächlich gelöscht wurden.
        meta_area_full = ", ".join(_ars) if _ars else "unbekannt"
        meta_stac_dt = _dts[0] if len(_dts) == 1 else (f"multi_{len(sel_objs)}" if _dts else "")
        auftragstyp  = self._auftragstyp_var.get().split("(")[0].strip()

        # Dateiname bewusst mit dem kompakten meta_area (ggf. "multi"), sonst
        # würde ein Dateiname bei vielen Areas unhandlich lang.
        session_logger = self._make_session_logger("STAC", env, meta_year, meta_area, meta_stac_dt)

        self._log_write(f"\n{'='*60}\n[{ts}] STAC LÖSCHUNG GESTARTET\n{'='*60}\n")
        self._log_write(
            f"Umgebung:        {env}\n"
            f"Collection:      {COLLECTION_ID}\n"
            f"Auftragstyp:     {auftragstyp}\n"
            f"Jahr:            {meta_year}\n"
            f"AREA:            {meta_area_full}\n"
            f"STAC-Datetime:   {meta_stac_dt or '(unbekannt)'}\n"
            f"Items:           {len(sel_ids)}  (davon {len(empty_items_to_delete)} bereits leer)"
            f"  |  Assets: {total}\n\n"
        )
        session_logger.info(
            f"[STAC START] {env} | {COLLECTION_ID} | Auftragstyp: {auftragstyp} | "
            f"Jahr: {meta_year} | AREA: {meta_area_full} | StacDatetime: {meta_stac_dt} | "
            f"Items: {len(sel_ids)} (leer: {len(empty_items_to_delete)}) | Assets: {total}")

        # Area je Item für die Log-Zeilen unten nachschlagbar
        area_by_item = {it["id"]: (stac_item_area(it) or "unbekannt") for it in sel_objs}

        for iid, asset_keys in selected_items.items():
            total_in_item = len(self._items_asset_hrefs.get(iid, {}))
            item_area = area_by_item.get(iid, "unbekannt")
            self._log_write(
                f"Item: {iid}  ({len(asset_keys)} von {total_in_item} Assets)  "
                f"|  Area: {item_area}\n")
            ok_for_item = 0

            for ak in asset_keys:
                http_code, reason = 0, ""
                try:
                    success, http_code, reason = delete_asset(
                        base_url, auth, iid, ak)
                    if not success and reason and "upload" in reason.lower() \
                            and "progress" in reason.lower():
                        success, http_code, reason = self._recover_stuck_upload(
                            base_url, auth, iid, ak, session_logger, env)
                except Exception as exc:
                    success = False
                    self._log_write(f"  [FEHLER] {ak}: {exc}\n")
                    session_logger.error(f"[STAC FEHLER] {env}/{iid}/{ak}: {exc}")

                if success:
                    ok_for_item += 1
                    ok_list.append(f"{iid}/{ak}")
                    ok_pairs.append((iid, ak))
                    self._log_write(f"  [OK]   gelöscht: {ak}  (HTTP {http_code})\n")
                    session_logger.info(f"[STAC OK]   {env}/{iid}/{ak}  HTTP {http_code}")
                else:
                    fail_list.append(f"{iid}/{ak}")
                    reason_txt = f"  – {reason}" if reason else ""
                    self._log_write(
                        f"  [FAIL] nicht gelöscht: {ak}  (HTTP {http_code}){reason_txt}\n")
                    session_logger.warning(
                        f"[STAC FAIL] {env}/{iid}/{ak}  HTTP {http_code}  {reason}")

                done += 1
                self.after(0, lambda d=done: self._progress.configure(value=d))

            # Item löschen falls alle Assets des gesamten Items erfolgreich gelöscht
            if ok_for_item == total_in_item:
                self._log_write(f"  → Item vollständig leer, wird gelöscht …\n")
                item_code, item_reason = 0, ""
                try:
                    item_ok, item_code, item_reason = delete_item(base_url, auth, iid)
                except Exception as exc:
                    item_ok = False
                    self._log_write(f"  [FEHLER] Item {iid}: {exc}\n")
                    session_logger.error(f"[STAC FEHLER] Item {env}/{iid}: {exc}")

                if item_ok:
                    items_deleted.append(iid)
                    self._log_write(f"  [OK]   Item gelöscht: {iid}  (HTTP {item_code})\n")
                    session_logger.info(f"[STAC OK]   Item {env}/{iid}  HTTP {item_code}")
                else:
                    items_del_fail.append(iid)
                    reason_txt = f"  – {item_reason}" if item_reason else ""
                    self._log_write(
                        f"  [FAIL] Item nicht gelöscht: {iid}  (HTTP {item_code}){reason_txt}\n")
                    session_logger.warning(
                        f"[STAC FAIL] Item {env}/{iid}  HTTP {item_code}  {item_reason}")

        # Items, die bereits ohne Assets ausgewählt wurden – direkt löschen,
        # da kein vorgelagerter Asset-Löschschritt nötig ist.
        for iid in empty_items_to_delete:
            item_area = area_by_item.get(iid, "unbekannt")
            self._log_write(
                f"Item: {iid}  (bereits ohne Assets, wird direkt gelöscht)  "
                f"|  Area: {item_area}\n")
            item_code, item_reason = 0, ""
            try:
                item_ok, item_code, item_reason = delete_item(base_url, auth, iid)
            except Exception as exc:
                item_ok = False
                self._log_write(f"  [FEHLER] Item {iid}: {exc}\n")
                session_logger.error(f"[STAC FEHLER] Item {env}/{iid}: {exc}")

            if item_ok:
                items_deleted.append(iid)
                self._log_write(f"  [OK]   Item gelöscht: {iid}  (HTTP {item_code})\n")
                session_logger.info(f"[STAC OK]   Item {env}/{iid}  HTTP {item_code}")
            else:
                items_del_fail.append(iid)
                reason_txt = f"  – {item_reason}" if item_reason else ""
                self._log_write(
                    f"  [FAIL] Item nicht gelöscht: {iid}  (HTTP {item_code}){reason_txt}\n")
                session_logger.warning(
                    f"[STAC FAIL] Item {env}/{iid}  HTTP {item_code}  {item_reason}")

            done += 1
            self.after(0, lambda d=done: self._progress.configure(value=d))

        ts2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_write(f"\n{'='*60}\n[{ts2}] ABGESCHLOSSEN\n"
                        f"  Assets erfolgreich:    {len(ok_list)}\n"
                        f"  Assets fehlgeschlagen: {len(fail_list)}\n")
        if items_deleted or items_del_fail:
            self._log_write(f"  Items gelöscht:        {len(items_deleted)}\n"
                            f"  Items fehlgeschlagen:  {len(items_del_fail)}\n")
        # Die serverseitige Such-/Katalog-Ansicht (GET .../items) UND der
        # direkte Asset-Download-Link hinken der transaktionalen Löschung
        # hinterher (Cache/Such-Index/CDN) - erfolgreich gelöschte Assets/
        # Items können nach "aktualisieren" deshalb kurzzeitig weiterhin
        # auftauchen bzw. herunterladbar bleiben, obwohl die Löschung (siehe
        # HTTP-Codes oben) bereits erfolgreich war. Per Test bestätigt: nach
        # ca. 15 Minuten sind sowohl Katalogeintrag als auch Download-Link
        # weg (kein Backend-Bug, reine Propagationszeit) – die vollen Cache-
        # Header (Age, X-Cache, …) landen bei "Assets prüfen" weiterhin im
        # Log (siehe _check_worker), nicht mehr in der STATUS-Spalte.
        delay_hint = ""
        if ok_list or items_deleted:
            delay_hint = (
                "\nHinweis: Änderungen können serverseitig einige Minuten "
                "(erfahrungsgemäss bis zu ca. 15 Minuten) benötigen, bis sie "
                "aus der Liste UND vom Asset-Download-Link verschwinden "
                "(Cache/Such-Index/CDN) - die Löschung selbst war bereits "
                "erfolgreich.\n"
            )
            self._log_write(delay_hint)
        self._log_write(f"{'='*60}\n")
        session_logger.info(
            f"[STAC END] Assets OK: {len(ok_list)} | FAIL: {len(fail_list)} | "
            f"Items gelöscht: {len(items_deleted)} | FAIL: {len(items_del_fail)}")

        item_summary = ""
        if items_deleted or items_del_fail:
            item_summary = (f"\n\nItems vollständig gelöscht:  {len(items_deleted)}\n"
                            f"Item-Löschung fehlgeschl.:   {len(items_del_fail)}")

        popup_hint = ""
        if ok_list or items_deleted:
            popup_hint = (
                "\n\nHinweis: Änderungen können serverseitig bis zu ca.\n"
                "15 Minuten benötigen, bis sie aus der Liste UND vom Asset-\n"
                "Download-Link verschwinden (Cache/Such-Index/CDN) - die\n"
                "Löschung selbst war bereits erfolgreich."
            )

        self.after(0, lambda: self._status_lbl.configure(
            text=f"Fertig: {len(ok_list)} OK  /  {len(fail_list)} Fehler"))
        self.after(0, self._enable_search_btns)
        self.after(0, lambda: self._set_env_controls_locked(False))
        self.after(0, lambda: self._remove_deleted_assets(ok_pairs, items_deleted))
        self.after(0, lambda: messagebox.showinfo(
            "STAC Löschung abgeschlossen",
            f"Assets erfolgreich:    {len(ok_list)}\n"
            f"Assets fehlgeschlagen: {len(fail_list)}"
            f"{item_summary}"
            f"{popup_hint}",
        ))

    def _remove_deleted_assets(self, ok_pairs: List[Tuple[str, str]],
                                items_deleted: List[str]):
        """Entfernt erfolgreich gelöschte Assets/Items aus Tree UND den
        zugrundeliegenden Datenstrukturen (_items_preview/_items_asset_hrefs/
        _items_assets), damit ein späterer Filterwechsel (_apply_filters()
        ruft _populate_tree() erneut auf) bereits gelöschte Zeilen nicht aus
        den noch alten Rohdaten wieder aufleben lässt."""
        touched_items = set()
        for iid, ak in ok_pairs:
            touched_items.add(iid)
            nid = f"asset::{iid}::{ak}"
            if self._tree.exists(nid):
                self._tree.delete(nid)
            self._nodes.pop(nid, None)
            self._checked.pop(nid, None)
            self._asset_status.pop((iid, ak), None)
            self._items_asset_hrefs.get(iid, {}).pop(ak, None)
            if iid in self._items_assets and ak in self._items_assets[iid]:
                self._items_assets[iid].remove(ak)

        for iid in items_deleted:
            touched_items.add(iid)
            node_id = f"item::{iid}"
            if self._tree.exists(node_id):
                self._tree.delete(node_id)
            self._nodes.pop(node_id, None)
            self._checked_items.pop(iid, None)
            self._items_preview = [it for it in self._items_preview if it["id"] != iid]
            self._items_asset_hrefs.pop(iid, None)
            self._items_assets.pop(iid, None)

        for iid in touched_items - set(items_deleted):
            self._refresh_item_glyph(iid)

        self._update_preview_label()

    # ═══════════════════════════════════════════════════════════════════════════
    # GDWH – Event Handler
    # ═══════════════════════════════════════════════════════════════════════════

    def _gdwh_on_env_change(self):
        env = self._gdwh_env_var.get()
        self._gdwh_url_lbl.configure(text=GDWH_ENVIRONMENTS[env])
        self._gdwh_base_url = GDWH_ENVIRONMENTS[env]
        self._gdwh_fetch_btn.config(state="normal")
        self._gdwh_reset_state()
        self._apply_theme(self._dark)

    def _gdwh_on_gds_key_change(self, _event=None):
        # Siehe _gdwh_reset_state(): die DataPackage-IDs einer geladenen Liste
        # gehören zum GDS-Key, unter dem sie geladen wurden – bei Wechsel des
        # GDS-Key sofort verwerfen, statt sie gegen den neuen Key stehen zu lassen.
        self._gdwh_reset_state()

    def _gdwh_fetch_imports(self):
        gds_key = self._gdwh_gds_key_var.get().strip()
        if not gds_key:
            messagebox.showwarning("Eingabe fehlt", "Bitte einen GDS-Key eingeben.")
            return
        self._gdwh_fetch_btn.config(state="disabled")
        self._gdwh_del_btn.config(state="disabled")
        self._gdwh_preview_lbl.configure(text="Lade Imports …")
        self._gdwh_clear_list()
        threading.Thread(target=self._gdwh_fetch_worker,
                         args=(gds_key,), daemon=True).start()

    def _gdwh_fetch_worker(self, gds_key: str):
        self._gdwh_current_gds_key = gds_key
        keys_to_load = GDWH_GDS_KEYS if gds_key == GDWH_ALL_GDS_OPTION else [gds_key]
        try:
            imports: List[Dict] = []
            enriched = []
            for key in keys_to_load:
                self._gdwh_log_write(f"[GDWH] Lade Imports für GDS-Key: {key} …\n")
                key_imports = gdwh_get_imports(self._gdwh_base_url, key)
                # gdsKey stammt zwar bereits aus der API-Antwort, wird hier aber
                # explizit gesetzt – so bleibt jedes Import-Objekt eindeutig
                # seinem GDS-Key zugeordnet (sicherheitskritisch bei "Alle GDS":
                # die Löschung braucht pro Package den passenden GDS-Key, siehe
                # _gdwh_delete_worker).
                for imp in key_imports:
                    imp["gdsKey"] = key
                imports.extend(key_imports)
                self._gdwh_log_write(f"[GDWH] {len(key_imports)} DataPackage(s) gefunden.\n")

                # FileMetadata laden und Imports damit anreichern (Area/Jahr/LineID
                # liegen dauerhaft im GDWH – unabhängig vom Ingest-Bucket, der nach
                # erfolgreichem Import regelmässig geleert wird).
                self._gdwh_log_write(f"[GDWH] Lade FileMetadata für GDS-Key: {key} …\n")
                file_metadata = gdwh_search_file_metadata(self._gdwh_base_url, key)
                self._gdwh_log_write(
                    f"[GDWH] {len(file_metadata)} FileMetadata-Eintrag/Einträge gefunden.\n")
                meta_index = gdwh_index_file_metadata_by_import(file_metadata)

                # Jedem Import die passenden FileMetadata-Attribute zuordnen
                for imp in key_imports:
                    match = meta_index.get(gdwh_import_id(imp))
                    enriched.append((imp, match))
                    if match:
                        self._gdwh_log_write(
                            f"  → {gdwh_import_date(imp)}  Jahr={match['year']}"
                            + (f"  [{match['area']}]" if match['area'] else "") + "\n")

            self._gdwh_imports = imports
            self._gdwh_enriched = enriched
            self.after(0, self._gdwh_apply_filter)

            pending_count = sum(1 for imp, match in enriched
                                 if match is None and _gdwh_is_pending(imp))
            if pending_count:
                self.after(50, lambda: self._gdwh_show_pending_notice(pending_count))
        except Exception as exc:
            self._gdwh_log_write(f"[FEHLER] {exc}\n")
            self.after(0, lambda: messagebox.showerror("GDWH Fehler", str(exc)))
            self.after(0, lambda: self._gdwh_fetch_btn.config(state="normal"))
            self.after(0, lambda: self._gdwh_preview_lbl.configure(
                text="Fehler beim Laden."))

    def _gdwh_show_pending_notice(self, count: int):
        """Info-Popup nach dem Laden: weist auf frisch importierte, noch nicht
        indexierte DataPackages hin (siehe _gdwh_is_pending/_GDWH_PENDING_HOURS).
        Läuft nur einmal pro Ladevorgang, nicht bei jeder Filteränderung."""
        messagebox.showinfo(
            "Frisch importierte DataPackages",
            f"{count} DataPackage(s) wurden erst kürzlich importiert. GDWH zeigt "
            f"sie im Portal zwar bereits an, aktualisiert seinen FileMetadata-Index "
            f"(Area/Jahr/Auftragstyp) aber zeitversetzt — das kann einige Stunden "
            f"dauern.\n\n"
            f"Diese Packages sind in der Liste mit „⏳ Frisch importiert“ markiert "
            f"und vorerst nicht auswählbar/löschbar.\n\n"
            f"Bitte in ein paar Stunden nochmals „{self._GDWH_FETCH_BTN_LABEL_RELOAD}“ "
            f"klicken, um den aktuellen Stand zu prüfen."
        )

    def _gdwh_toggle_leichen(self):
        active = not self._gdwh_show_leichen_var.get()
        self._gdwh_show_leichen_var.set(active)
        self._gdwh_leichen_btn.config(
            text=self._GDWH_LEICHEN_BTN_LABEL_BACK if active
                 else self._GDWH_LEICHEN_BTN_LABEL)
        self._gdwh_apply_filter()

    def _gdwh_apply_filter(self):
        if not hasattr(self, "_gdwh_enriched"):
            return
        # Drei Kategorien pro Import:
        #   - "aktiv":   FileMetadata-Match vorhanden → normale Anzeige.
        #   - "pending": kein Match, aber Import jünger als _GDWH_PENDING_HOURS
        #                → GDWH hat die Attribute nur noch nicht nachindexiert,
        #                keine Anomalie (live verifiziert 2026-08-20).
        #   - "anomalie": kein Match und älter als _GDWH_PENDING_HOURS → GDWH
        #                hat dafür (noch) keinen FileMetadata-Eintrag geliefert.
        # WICHTIG: alle drei Kategorien werden in der Normalansicht IMMER
        # angezeigt (jede reale /data/imports-Zeile bleibt sichtbar, auch ohne
        # FileMetadata-Match – ein fehlender Match beweist nicht, dass die
        # Daten nicht mehr in GDWH existieren, siehe Portal-Abgleich). Sie
        # bleiben aber nicht auswählbar/löschbar (deletable erfordert match is
        # not None, siehe _gdwh_populate_list) – nur die reine Sichtbarkeit
        # wird hier gefixt, die Lösch-Sicherheitssperre bleibt unverändert.
        # Der Anomalien-Toggle filtert zusätzlich auf NUR "anomalie", er
        # blendet in der Normalansicht nichts aus.
        leichen_mode = self._gdwh_show_leichen_var.get()
        self._gdwh_leichen_btn.config(
            state="normal" if self._gdwh_enriched else "disabled")

        def _is_anomalie(item):
            imp, match = item
            return match is None and not _gdwh_is_pending(imp)

        if leichen_mode:
            base = [item for item in self._gdwh_enriched if _is_anomalie(item)]
        else:
            base = list(self._gdwh_enriched)

        typ_filter = AUFTRAGSTYPEN.get(self._gdwh_auftragstyp_var.get(), "").strip().lower()
        year = self._gdwh_year_filter_var.get().strip()
        data = base
        if typ_filter:
            def _typ_matches(item):
                _imp, match = item
                auftragstyp = (match.get("auftragstyp", "") if match else "").strip().lower()
                # Kein Match / kein Auftragstyp-Attribut (z.B. FileMetadata
                # noch nicht angereichert): analog zum Jahresfilter unten
                # NICHT ausblenden, sonst verschwinden Packages ohne
                # auswertbare FileMetadata spurlos aus der gefilterten Liste.
                if not auftragstyp:
                    return True
                return typ_filter in auftragstyp
            data = [item for item in data if _typ_matches(item)]
        if year:
            def _year_matches(item):
                imp, match = item
                if match:
                    for src in (match.get("stac_datetime", ""), match.get("year", "")):
                        m = re.search(r"(?<!\d)(20\d{2})(?!\d)", src)
                        if m:
                            return m.group(1) == year
                m = re.search(r"(?<!\d)(20\d{2})(?!\d)", gdwh_import_date(imp))
                if m:
                    return m.group(1) == year
                # Kein Bucket-Match (z.B. Import-Ordner bereits geleert) und
                # kein auswertbares importDate: Jahr unbekannt. Package trotz
                # aktivem Jahresfilter NICHT ausblenden – sonst verschwinden
                # tatsächlich im GDWH vorhandene Packages spurlos aus der
                # Liste, sobald der Ingest-Bucket regelmässig geleert wurde.
                # Wird in der Liste als "????" markiert, siehe _year_key/-
                # _gdwh_populate_list.
                return True
            data = [item for item in data if _year_matches(item)]
        area = self._gdwh_area_filter_var.get().strip()
        if area:
            def _area_matches(item):
                _imp, match = item
                area_val = (match.get("area", "") if match else "").strip()
                # Kein Match / kein AREA-Attribut: analog zu Jahr/Auftragstyp
                # NICHT ausblenden, sonst verschwinden Packages ohne
                # auswertbare FileMetadata spurlos aus der gefilterten Liste.
                if not area_val:
                    return True
                return area_val.strip().lower() == area.lower()
            data = [item for item in data if _area_matches(item)]
        self._gdwh_total_leichen = sum(
            1 for item in self._gdwh_enriched if _is_anomalie(item))
        self._gdwh_total_pending = sum(
            1 for imp, match in self._gdwh_enriched
            if match is None and _gdwh_is_pending(imp))
        self._gdwh_populate_list(data, leichen_mode=leichen_mode)

    def _gdwh_reset_state(self):
        # Sicherheitskritisch: bei Umgebungs- oder GDS-Key-Wechsel muss die
        # bereits geladene DataPackage-Liste (inkl. Auswahl) sofort verworfen
        # werden. Sonst würde eine gegen den ALTEN Kontext geladene Auswahl
        # gegen den NEU gewählten Kontext gelöscht (_gdwh_base_url/gds_key
        # werden bei der Löschung stets frisch/aktuell gelesen).
        self._gdwh_enriched = []
        self._gdwh_total_leichen = 0
        self._gdwh_total_pending = 0
        self._gdwh_show_leichen_var.set(False)
        self._gdwh_leichen_btn.config(
            text=self._GDWH_LEICHEN_BTN_LABEL, state="disabled")
        self._gdwh_clear_list()
        self._gdwh_del_btn.config(
            text="Ausgewählte DataPackages löschen …", state="disabled")
        self._gdwh_preview_lbl.configure(text="Noch keine Imports geladen.")
        # Neuer Kontext (Umgebung/GDS-Key) = noch nichts geladen: Button-Text
        # auf Ausgangstext zurücksetzen, sonst würde fälschlich "aktualisieren"
        # stehen (analog zu _on_env_change() im STAC-Tab).
        self._gdwh_loaded_once = False
        self._gdwh_fetch_btn.config(text=self._GDWH_FETCH_BTN_LABEL, style="TButton")

    def _gdwh_clear_list(self):
        for w in self._gdwh_list_frame.winfo_children():
            w.destroy()
        self._gdwh_selection.clear()
        self._gdwh_row_widgets.clear()

    def _gdwh_copy_uuid(self, uuid: str, button: tk.Button):
        """Kopiert die Import-UUID in die Zwischenablage, kurzes visuelles Feedback."""
        self.clipboard_clear()
        self.clipboard_append(uuid)
        orig_text = button.cget("text")
        button.config(text="✓ Kopiert")
        self.after(1200, lambda: button.config(text=orig_text)
                   if button.winfo_exists() else None)

    def _gdwh_populate_list(self, enriched: List[Tuple], leichen_mode: bool = False):
        self._gdwh_clear_list()
        T = DARK if self._dark else LIGHT
        self._gdwh_total_loaded = len(enriched)

        if not enriched:
            tk.Label(
                self._gdwh_list_frame,
                text=("Keine GDWH-Anomalien gefunden." if leichen_mode
                      else "Keine DataPackages gefunden."),
                font=("Segoe UI", 9, "italic"),
                bg=T["chk_bg"], fg=T["fg_dim"], padx=8, pady=8,
            ).pack(anchor="w")
            self._gdwh_loaded_once = True
            self._gdwh_fetch_btn.config(
                state="normal", text=self._GDWH_FETCH_BTN_LABEL_RELOAD,
                style="Amber.TButton")
            self._gdwh_preview_lbl.configure(
                text="0 GDWH-Anomalien gefunden." if leichen_mode
                     else "0 DataPackages gefunden.")
            return

        def _year_key(item):
            """Sortierschlüssel: Jahr aus stac_datetime > Ordnername > importDate."""
            imp, match = item
            if match:
                for src in (match.get("stac_datetime", ""), match.get("year", "")):
                    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", src)
                    if m:
                        return int(m.group(1))
            m = re.search(r"(?<!\d)(20\d{2})(?!\d)", gdwh_import_date(imp))
            return int(m.group(1)) if m else 0

        for imp, match in sorted(enriched, key=_year_key, reverse=True):
            pkg_id     = gdwh_import_id(imp)
            pkg_date   = gdwh_import_date(imp)
            pkg_bbox   = gdwh_import_footprint_bbox(imp)
            pkg_status = gdwh_import_status(imp)
            # GET /data/imports liefert in der Praxis GAR KEIN Status-Feld
            # (nur uuid/gdsKey/importDate/footprint – per Swagger verifiziert),
            # gdwh_import_status() liefert daher fast immer "?". Nur wenn ein
            # ECHTER, bekannter Status zurückkommt und der NICHT "Imported"
            # ist, sperren wir die Checkbox präventiv (DELETE würde ohnehin
            # ablehnen, siehe Fehlermeldung "must have status 'Imported'").
            # Bei "?" (Normalfall) gilt: löschbar, bis der server-seitige
            # DELETE-Aufruf das Gegenteil zeigt (dann als [FAIL] im Log).
            deletable_by_status = pkg_status == "?" or pkg_status.lower() == "imported"
            # Ohne FileMetadata-Match ist entweder (a) der Import noch nicht
            # indexiert ("pending", siehe _gdwh_is_pending) – dann kennen wir
            # seine Attribute schlicht noch nicht – oder (b) er ist eine echte
            # Anomalie und existiert vermutlich nicht mehr wirklich in GDWH
            # (siehe _gdwh_apply_filter). In beiden Fällen sperren wir die
            # Auswahl: bei (a) fehlen die Daten fürs sichere Löschen noch, bei
            # (b) wäre ein DELETE ein No-Op, das GDWH trotzdem als "Erfolg"
            # quittiert, ohne dass es etwas zu löschen gäbe.
            pending    = match is None and _gdwh_is_pending(imp)
            deletable  = deletable_by_status and match is not None

            auftragstyp   = match.get("auftragstyp", "")  if match else ""
            area          = match.get("area", "")          if match else ""
            stac_datetime = match.get("stac_datetime", "") if match else ""
            commentary    = match.get("commentary", "")    if match else ""

            # Jahr für Anzeige: stac_datetime > Ordnername > importDate
            year = ""
            if match:
                for src in (stac_datetime, match.get("year", "")):
                    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", src)
                    if m:
                        year = m.group(1)
                        break
            if not year:
                m = re.search(r"(?<!\d)(20\d{2})(?!\d)", pkg_date)
                year = m.group(1) if m else ""

            area_color  = T["accent"] if area else T["fg_dim"]
            area_suffix = ""

            var = tk.BooleanVar(value=False)
            if deletable:
                # Nur löschbare (status == 'Imported') Packages landen in der
                # Auswahl-Map – so können "Alle auswählen" und der Start der
                # Löschung ein nicht mehr löschbares Package gar nicht erst
                # erfassen, unabhängig vom (gesperrten) Checkbox-Widget.
                var.trace_add("write", lambda *_: self._gdwh_on_checkbox_change())
                self._gdwh_selection[pkg_id] = var

            # Container für alle 3 Zeilen dieses Packages – erlaubt, die Zeile
            # nach erfolgreicher Löschung gezielt zu entfernen (statt die ganze
            # Liste neu zu laden), siehe _gdwh_remove_deleted_rows().
            entry_frame = tk.Frame(self._gdwh_list_frame, bg=T["chk_bg"])
            entry_frame.pack(fill="x")
            self._gdwh_row_widgets[pkg_id] = entry_frame

            # ── Zeile 1: Jahr  AREA  GDS-Key  Status ─────────────────────────
            row1 = tk.Frame(entry_frame, bg=T["chk_bg"])
            row1.pack(fill="x", padx=6, pady=(5, 0))

            tk.Checkbutton(
                row1, variable=var, state="normal" if deletable else "disabled",
                bg=T["chk_bg"], fg=T["fg"], selectcolor=T["input"],
                activebackground=T["chk_bg"], activeforeground=T["fg"],
            ).pack(side="left")

            year_display = year if year else ("NEU" if pending else "????")
            tk.Label(
                row1, text=year_display,
                font=("Cascadia Mono", 9, "bold"),
                bg=T["chk_bg"],
                fg=T["hint"] if pending else (T["fg"] if deletable else T["fg_dim"]),
                anchor="w", width=5,
            ).pack(side="left")

            tk.Label(
                row1, text=(area + area_suffix) if area else pkg_id[:12] + "…",
                font=("Cascadia Mono", 9, "bold"),
                bg=T["chk_bg"],
                fg=(area_color if area else T["fg_dim"]) if deletable else T["fg_dim"],
                anchor="w",
            ).pack(side="left")

            gds_key = imp.get("gdsKey") or getattr(self, "_gdwh_current_gds_key", "")
            file_format = match.get("file_format", "") if match else ""
            gds_key_label = f"[{gds_key}" + (f" · {file_format}]" if file_format else "]")
            if gds_key:
                tk.Label(
                    row1, text=f"    {gds_key_label}",
                    font=("Cascadia Mono", 8),
                    bg=T["chk_bg"], fg=T["fg_dim"], anchor="w",
                ).pack(side="left")

            if not deletable_by_status:
                tk.Label(
                    row1, text=f"    ⚠ Status: {pkg_status}  (nicht löschbar)",
                    font=("Segoe UI", 8, "italic"),
                    bg=T["chk_bg"], fg=T["hint"], anchor="w",
                ).pack(side="left")

            if match is None:
                if pending:
                    tk.Label(
                        row1,
                        text=f"    ⏳ Frisch importiert — Attribute (Area/Jahr) "
                             f"folgen, sobald GDWH den Index aktualisiert hat "
                             f"— Import-UUID: {pkg_id}",
                        font=("Segoe UI", 8, "bold"),
                        bg=T["chk_bg"], fg=T["hint"], anchor="w",
                    ).pack(side="left")
                else:
                    tk.Label(
                        row1,
                        text=f"    ⚠ Kein FileMetadata-Match seit über "
                             f"{_GDWH_PENDING_HOURS}h — nicht löschbar, bis "
                             f"geklärt ist ob die Daten noch existieren "
                             f"— Import-UUID: {pkg_id}",
                        font=("Segoe UI", 8, "bold"),
                        bg=T["chk_bg"], fg=T["err"], anchor="w",
                    ).pack(side="left")
                copy_btn = tk.Button(
                    row1, text="⧉ Kopieren", font=("Segoe UI", 7),
                    bg=T["btn"], fg=T["fg"], relief="flat",
                    padx=4, pady=0, cursor="hand2",
                )
                copy_btn.config(
                    command=lambda uuid=pkg_id, b=copy_btn: self._gdwh_copy_uuid(uuid, b))
                copy_btn.pack(side="left", padx=(4, 0))

            # ── Zeile 2 (eingerückt): Auftragstyp  StacItemIdDatetime ────────
            row2 = tk.Frame(entry_frame, bg=T["chk_bg"])
            row2.pack(fill="x", padx=30, pady=0)

            if auftragstyp:
                tk.Label(
                    row2, text=auftragstyp,
                    font=("Cascadia Mono", 8, "bold"),
                    bg=T["chk_bg"], fg=T["ok"], anchor="w",
                ).pack(side="left")

            if stac_datetime:
                tk.Label(
                    row2, text=("    " if auftragstyp else "") + stac_datetime,
                    font=("Segoe UI", 8),
                    bg=T["chk_bg"], fg=T["fg_dim"], anchor="w",
                ).pack(side="left")
            elif not auftragstyp and pkg_bbox:
                tk.Label(
                    row2, text=pkg_bbox,
                    font=("Segoe UI", 8),
                    bg=T["chk_bg"], fg=T["fg_dim"], anchor="w",
                ).pack(side="left")

            # ── Zeile 3 (eingerückt): Commentary  Import-Datum ──────────────
            row3 = tk.Frame(entry_frame, bg=T["chk_bg"])
            row3.pack(fill="x", padx=30, pady=(0, 2))

            parts3 = []
            if commentary:
                parts3.append(commentary)
            parts3.append(pkg_date)

            tk.Label(
                row3, text="   ·   ".join(parts3),
                font=("Segoe UI", 8),
                bg=T["chk_bg"], fg=T["fg_dim"], anchor="w",
            ).pack(side="left")

        self._gdwh_loaded_once = True
        self._gdwh_fetch_btn.config(
            state="normal", text=self._GDWH_FETCH_BTN_LABEL_RELOAD,
            style="Amber.TButton")
        st = "normal" if (enriched and not leichen_mode) else "disabled"
        self._gdwh_sel_all_btn.config(state=st)
        self._gdwh_sel_none_btn.config(state=st)
        self._gdwh_on_checkbox_change()
        self._apply_theme(self._dark)

    def _gdwh_on_checkbox_change(self):
        self._gdwh_update_preview()

    def _gdwh_select_all(self):
        for var in self._gdwh_selection.values():
            var.set(True)

    def _gdwh_deselect_all(self):
        for var in self._gdwh_selection.values():
            var.set(False)

    def _gdwh_update_preview(self):
        total        = getattr(self, "_gdwh_total_loaded", len(self._gdwh_selection))
        leichen_mode = self._gdwh_show_leichen_var.get()
        total_leichen = getattr(self, "_gdwh_total_leichen", 0)
        total_pending = getattr(self, "_gdwh_total_pending", 0)

        if leichen_mode:
            self._gdwh_preview_lbl.configure(
                text=f"{total} GDWH-Anomalie(n) — seit über {_GDWH_PENDING_HOURS}h "
                     f"ohne FileMetadata-Match (Filter auf diese Kategorie). "
                     f"Nicht auswählbar/löschbar, bis der Match geklärt ist."
            )
            self._gdwh_del_btn.config(
                text="Ausgewählte DataPackages (0) löschen …", state="disabled")
            self._apply_theme(self._dark)
            return

        deletable    = len(self._gdwh_selection)
        selected     = sum(v.get() for v in self._gdwh_selection.values())
        # total - deletable enthält status-gesperrte, pending (noch nicht
        # indexierte) UND Anomalie-Packages (kein FileMetadata-Match) – beide
        # Kategorien werden separat ausgewiesen, damit "gesperrt (Status ≠
        # Imported)" nicht fälschlich auf frische Importe oder Anomalien
        # zutrifft, die aus anderen Gründen (noch) nicht löschbar sind.
        locked_status = total - deletable - total_pending - total_leichen
        locked_note  = f"  |  {locked_status} gesperrt (Status ≠ Imported)" \
                       if locked_status > 0 else ""
        pending_note = f"  |  {total_pending} frisch importiert, noch nicht indexiert" \
                        if total_pending else ""
        leichen_note = f"  |  {total_leichen} ohne FileMetadata-Match, nicht löschbar " \
                        f"(Button „{self._GDWH_LEICHEN_BTN_LABEL}“ zum Filtern)" \
                        if total_leichen else ""
        self._gdwh_preview_lbl.configure(
            text=f"{total} DataPackage(s) geladen  |  {selected} ausgewählt zum Löschen"
                 f"{locked_note}{pending_note}{leichen_note}"
        )
        self._gdwh_del_btn.config(
            text=f"Ausgewählte DataPackages ({selected}) löschen …",
            state="normal" if selected > 0 else "disabled",
        )
        self._apply_theme(self._dark)

    # ── GDWH Löschung ─────────────────────────────────────────────────────────

    def _gdwh_start_deletion(self):
        selected = {
            pkg_id: var.get()
            for pkg_id, var in self._gdwh_selection.items()
            if var.get()
        }
        if not selected:
            messagebox.showwarning("Nichts ausgewählt", "Keine DataPackages ausgewählt.")
            return

        gds_key = self._gdwh_gds_key_var.get().strip()
        env     = self._gdwh_env_var.get()
        email   = self._gdwh_email_var.get().strip()

        dlg = GDWHConfirmDialog(self, env, gds_key, len(selected), self._dark)
        if not dlg.result:
            self._gdwh_log_write("[Abbruch] Löschung durch Benutzer abgebrochen.\n")
            return

        self._gdwh_del_btn.config(state="disabled")
        self._gdwh_fetch_btn.config(state="disabled")
        self._set_gdwh_env_controls_locked(True)
        self._gdwh_progress["maximum"] = len(selected)
        self._gdwh_progress["value"]   = 0
        self._gdwh_status_lbl.configure(text="Lösche …")
        self._apply_theme(self._dark)

        # base_url JETZT einfrieren (siehe _start_deletion für STAC): GDWH hat
        # keinen Credentials-Schritt, ein Umgebungs-Radiobutton würde
        # self._gdwh_base_url sonst live mitten in der Löschung ändern.
        base_url = self._gdwh_base_url
        threading.Thread(
            target=self._gdwh_delete_worker,
            args=(list(selected.keys()), gds_key, email, base_url),
            daemon=True,
        ).start()

    def _gdwh_delete_worker(self, pkg_ids: List[str], gds_key: str, email: str,
                             base_url: str):
        ok_list   = []
        fail_list = []
        ts        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        env       = self._gdwh_env_var.get()

        # Metadaten aus ausgewählten Packages für Log-Dateiname und Protokoll
        enriched_map = {gdwh_import_id(imp): (imp, match)
                        for imp, match in getattr(self, "_gdwh_enriched", [])}
        sel_enriched = [enriched_map[pid] for pid in pkg_ids if pid in enriched_map]

        _yrs, _ars, _dts, _typs = [], [], [], []
        for imp, match in sel_enriched:
            year_found = False
            if match:
                for src in (match.get("stac_datetime", ""), match.get("year", "")):
                    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", src)
                    if m:
                        y = m.group(1)
                        if y not in _yrs:
                            _yrs.append(y)
                        year_found = True
                        break
                if match.get("area") and match["area"] not in _ars:
                    _ars.append(match["area"])
                if match.get("stac_datetime") and match["stac_datetime"] not in _dts:
                    _dts.append(match["stac_datetime"])
                if match.get("auftragstyp") and match["auftragstyp"] not in _typs:
                    _typs.append(match["auftragstyp"])
            if not year_found:
                m = re.search(r"(?<!\d)(20\d{2})(?!\d)", gdwh_import_date(imp))
                if m and m.group(1) not in _yrs:
                    _yrs.append(m.group(1))

        meta_year        = _yrs[0] if len(_yrs) == 1 else ("multi" if _yrs else "unbekannt")
        meta_area        = _ars[0] if len(_ars) == 1 else ("multi" if _ars else "unbekannt")
        # Für Logausgabe (nicht Dateiname): alle betroffenen Areas einzeln
        # auflisten statt zu "multi" zusammenzufassen – sonst ist im Log
        # nicht mehr nachvollziehbar, welche Areas tatsächlich gelöscht wurden.
        meta_area_full   = ", ".join(_ars) if _ars else "unbekannt"
        meta_stac_dt     = _dts[0] if len(_dts) == 1 else (f"multi_{len(pkg_ids)}" if _dts else "")
        meta_auftragstyp = _typs[0] if _typs else ""

        # Dateiname bewusst mit dem kompakten meta_area (ggf. "multi"), sonst
        # würde ein Dateiname bei vielen Areas unhandlich lang.
        session_logger = self._make_session_logger("GDWH", env, meta_year, meta_area, meta_stac_dt)

        self._gdwh_log_write(
            f"\n{'='*60}\n[{ts}] GDWH LÖSCHUNG GESTARTET\n{'='*60}\n"
            f"Umgebung:        {env}\n"
            f"GDS-Key:         {gds_key}\n"
            f"Auftragstyp:     {meta_auftragstyp or '(unbekannt)'}\n"
            f"Jahr:            {meta_year}\n"
            f"AREA:            {meta_area_full}\n"
            f"STAC-Datetime:   {meta_stac_dt or '(unbekannt)'}\n"
            f"Packages:        {len(pkg_ids)}\n"
            f"E-Mail:          {email or '(keine)'}\n\n"
        )
        session_logger.info(
            f"[GDWH START] {env} | {gds_key} | Auftragstyp: {meta_auftragstyp} | "
            f"Jahr: {meta_year} | AREA: {meta_area_full} | StacDatetime: {meta_stac_dt} | "
            f"Packages: {len(pkg_ids)}")

        # Area/GDS-Key je Package für die Einzelzeilen (OK/FAIL) unten
        # nachschlagbar. Der GDS-Key kommt bewusst aus dem geladenen Import
        # (imp["gdsKey"]) statt aus dem einen Dropdown-Wert: bei "Alle GDS"
        # steht dort nur das Sentinel, und jedes Package muss beim DELETE-
        # Aufruf mit seinem tatsächlichen GDS-Key adressiert werden.
        area_by_pkg = {pid: ((m.get("area", "") if m else "") or "unbekannt")
                       for pid, (_imp, m) in enriched_map.items()}
        gds_key_by_pkg = {pid: (imp.get("gdsKey") or gds_key)
                          for pid, (imp, _m) in enriched_map.items()}

        unclear_list = []  # Jobs ohne eindeutigen Endstatus nach Timeout (siehe unten)

        # Phase 1: ALLE Lösch-Jobs zuerst starten (statt pro Package erst auf
        # den vorherigen Job zu warten) – die Jobs laufen serverseitig
        # asynchron/unabhängig voneinander, serielles Warten würde eine
        # Batch-Löschung unnötig auf die Summe aller Einzel-Laufzeiten
        # aufblähen statt auf die längste.
        job_id_by_pkg: Dict[str, str] = {}
        for pkg_id in pkg_ids:
            pkg_area    = area_by_pkg.get(pkg_id, "unbekannt")
            pkg_gds_key = gds_key_by_pkg.get(pkg_id, gds_key)
            try:
                job = gdwh_delete_import(base_url, pkg_gds_key, pkg_id, email)
                job_id = job.get("id", "?")
                job_id_by_pkg[pkg_id] = job_id
                self._gdwh_log_write(
                    f"  Löschjob gestartet: {pkg_id}  |  GDS-Key: {pkg_gds_key}"
                    f"  |  Area: {pkg_area}  |  Job-ID: {job_id}\n")
                session_logger.info(
                    f"[GDWH JOB START] {env}/{pkg_gds_key}/{pkg_id}  Area: {pkg_area}  Job: {job_id}")
            except Exception as exc:
                self._gdwh_log_write(
                    f"  [FAIL] Package: {pkg_id}  |  GDS-Key: {pkg_gds_key}"
                    f"  |  Area: {pkg_area}  →  {exc}\n")
                session_logger.warning(
                    f"[GDWH FAIL] {env}/{pkg_gds_key}/{pkg_id}  Area: {pkg_area}  →  {exc}")
                fail_list.append(pkg_id)

        # Phase 2: alle gestarteten Jobs interleaved pollen (GET /api/jobs/
        # {jobId}), statt den DELETE-Request selbst als "erledigt" zu werten
        # – jeder Job läuft serverseitig asynchron weiter.
        done_count = [0]

        def _on_poll(_pkg_id, job):
            status = str(job.get("status", "")).strip().lower()
            if status in GDWH_JOB_STATUS_SUCCESS or status in GDWH_JOB_STATUS_FAILURE:
                done_count[0] += 1
                self.after(0, lambda v=done_count[0]: self._gdwh_progress.configure(value=v))
            self.after(0, lambda: self._gdwh_status_lbl.configure(
                text=f"Warte auf GDWH-Jobs … {done_count[0]}/{len(job_id_by_pkg)} abgeschlossen"))

        final_jobs = gdwh_wait_for_jobs(
            base_url, job_id_by_pkg,
            timeout=_GDWH_JOB_POLL_TIMEOUT, interval=_GDWH_JOB_POLL_INTERVAL,
            on_poll=_on_poll,
        ) if job_id_by_pkg else {}

        # Phase 3: Ergebnis je Package auswerten; Bucket-Cleanup NUR nach
        # bestätigtem Job-Erfolg (sonst würde ein noch laufender Lösch-Job
        # durch den Cleanup-Aufruf gestört – siehe Kommentar unten).
        for pkg_id, job_id in job_id_by_pkg.items():
            pkg_area    = area_by_pkg.get(pkg_id, "unbekannt")
            pkg_gds_key = gds_key_by_pkg.get(pkg_id, gds_key)
            final_job    = final_jobs.get(pkg_id, {})
            final_status = str(final_job.get("status", "")).strip().lower()

            if final_status in GDWH_JOB_STATUS_FAILURE:
                result = final_job.get("result") or final_job.get("log") or ""
                self._gdwh_log_write(
                    f"  [FAIL] Job meldet Fehlschlag: {pkg_id}  |  Status: {final_status}"
                    f"  →  {result}\n")
                session_logger.warning(
                    f"[GDWH JOB FAILED] {env}/{pkg_gds_key}/{pkg_id}  Job: {job_id}  →  {result}")
                fail_list.append(pkg_id)
                continue

            if final_status not in GDWH_JOB_STATUS_SUCCESS:
                self._gdwh_log_write(
                    f"  [UNKLAR] Job noch nicht abgeschlossen nach "
                    f"{_GDWH_JOB_POLL_TIMEOUT:.0f}s: {pkg_id}  |  letzter Status: "
                    f"{final_job.get('status', '?')}  |  Abschluss/Bucket-Aufräumen bitte "
                    f"manuell per E-Mail/GDWH-Portal prüfen.\n")
                session_logger.warning(
                    f"[GDWH JOB TIMEOUT] {env}/{pkg_gds_key}/{pkg_id}  Job: {job_id}  "
                    f"letzter Status: {final_job.get('status', '?')}")
                unclear_list.append(pkg_id)
                continue

            self._gdwh_log_write(
                f"  [OK]  Job erfolgreich abgeschlossen: {pkg_id}  |  Status: "
                f"{final_job.get('status', '?')}\n")
            session_logger.info(
                f"[GDWH OK] {env}/{pkg_gds_key}/{pkg_id}  Area: {pkg_area}  Job: {job_id}")
            ok_list.append(pkg_id)

            # Bucket aufräumen: falls noch ein DataPackage-Ordner mit
            # derselben ID im Ingest-Bucket liegt (Import erfolgt, aber nie
            # aufgeräumt), jetzt zusätzlich löschen – sonst bleibt das
            # Package im GDWH-Portal als "hängendes" DataPackage sichtbar
            # und blockiert einen sauberen Neu-Import.
            try:
                cleaned = gdwh_cleanup_data_package(base_url, pkg_gds_key, pkg_id)
                if cleaned is not None:
                    self._gdwh_log_write(
                        "        Bucket-DataPackage ebenfalls gelöscht.\n")
                    session_logger.info(
                        f"[GDWH BUCKET CLEANUP OK] {env}/{pkg_gds_key}/{pkg_id}")
            except Exception as cleanup_exc:
                self._gdwh_log_write(
                    f"        [WARNUNG] Bucket-DataPackage konnte nicht "
                    f"gelöscht werden: {cleanup_exc}\n")
                session_logger.warning(
                    f"[GDWH BUCKET CLEANUP FAIL] {env}/{pkg_gds_key}/{pkg_id}  "
                    f"→  {cleanup_exc}")

        ts2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._gdwh_log_write(
            f"\n{'='*60}\n[{ts2}] ABGESCHLOSSEN\n"
            f"  Erfolgreich bestätigt: {len(ok_list)}\n"
            f"  Fehlgeschlagen:        {len(fail_list)}\n"
            f"  Status unklar/Timeout: {len(unclear_list)}\n"
            f"{'='*60}\n"
        )
        session_logger.info(
            f"[GDWH END] OK: {len(ok_list)} | FAIL: {len(fail_list)} | "
            f"UNKLAR: {len(unclear_list)}")

        note = ""
        if unclear_list:
            note = ("\n\nHinweis: Bei manchen Packages konnte der Job-Abschluss nicht "
                     f"innert {_GDWH_JOB_POLL_TIMEOUT:.0f}s bestätigt werden – Status bitte "
                     "per E-Mail-Benachrichtigung oder im GDWH-Portal prüfen.")
        elif ok_list and not email:
            note = "\n\nHinweis: Ohne E-Mail-Adresse keine zusätzliche Abschluss-Benachrichtigung."

        self.after(0, lambda: self._gdwh_progress.configure(value=len(pkg_ids)))
        self.after(0, lambda: self._gdwh_status_lbl.configure(
            text=f"Fertig: {len(ok_list)} OK  /  {len(fail_list)} Fehler  /  "
                 f"{len(unclear_list)} unklar"))
        self.after(0, lambda: self._gdwh_fetch_btn.config(state="normal"))
        self.after(0, lambda: self._set_gdwh_env_controls_locked(False))
        self.after(0, lambda: self._gdwh_remove_deleted_rows(ok_list))
        self.after(0, lambda: messagebox.showinfo(
            "GDWH Löschung abgeschlossen",
            f"Erfolgreich bestätigt: {len(ok_list)}\n"
            f"Fehlgeschlagen:        {len(fail_list)}\n"
            f"Status unklar/Timeout: {len(unclear_list)}"
            f"{note}",
        ))

    def _gdwh_remove_deleted_rows(self, ok_pkg_ids: List[str]):
        """Entfernt erfolgreich zum Löschen eingereichte Packages aus der
        Liste UND aus _gdwh_enriched/_gdwh_imports, damit ein späterer
        Jahres-Filterwechsel (_gdwh_apply_filter() ruft _gdwh_populate_list()
        erneut auf) sie nicht aus den noch alten Rohdaten wieder aufleben
        lässt. Die GDWH-Löschung läuft serverseitig asynchron (Job) – ein
        erneutes Anzeigen/Anklicken hier wäre ohnehin nur ein redundanter
        zweiter Löschauftrag auf dasselbe Package."""
        ok_ids = set(ok_pkg_ids)
        if not ok_ids:
            return
        for pkg_id in ok_ids:
            w = self._gdwh_row_widgets.pop(pkg_id, None)
            if w is not None:
                w.destroy()
            self._gdwh_selection.pop(pkg_id, None)

        if hasattr(self, "_gdwh_enriched"):
            self._gdwh_enriched = [
                (imp, match) for imp, match in self._gdwh_enriched
                if gdwh_import_id(imp) not in ok_ids
            ]
        self._gdwh_imports = [
            imp for imp in self._gdwh_imports if gdwh_import_id(imp) not in ok_ids
        ]

        self._gdwh_update_preview()

    # ═══════════════════════════════════════════════════════════════════════════
    # Hilfsfunktionen
    # ═══════════════════════════════════════════════════════════════════════════

    def _clear_state(self):
        self._items_preview     = []
        self._items_asset_hrefs = {}
        self._items_assets      = {}
        self._nodes             = {}
        self._checked           = {}
        self._checked_items     = {}
        self._asset_status      = {}
        self._show_faulty_only  = False
        self._show_faulty_btn.config(text=self._SHOW_FAULTY_BTN_LABEL)
        self._show_no_thumb_only = False
        self._show_no_thumb_btn.config(text=self._SHOW_NO_THUMB_BTN_LABEL)
        self._check_btn.config(style="Amber.TButton")

    def _disable_search_btns(self):
        self._load_btn.config(state="disabled")

    def _enable_search_btns(self):
        self._load_btn.config(state="normal")

    def _set_env_controls_locked(self, locked: bool):
        # Während eine Löschung läuft, dürfen Umgebung und Credentials nicht
        # wechselbar sein: base_url/auth wurden zwar beim Start eingefroren
        # (siehe _start_deletion), aber ein Wechsel mitten in der Löschung
        # wäre trotzdem irreführend/gefährlich für die Bedienperson.
        state = "disabled" if locked else "normal"
        for rb in self._env_radios:
            rb.config(state=state)
        self._cred_btn.config(state=state)

    def _set_gdwh_env_controls_locked(self, locked: bool):
        # Analog zu _set_env_controls_locked(): GDWH hat keinen separaten
        # "Credentials laden"-Schritt (SSPI läuft transparent) – ein Klick auf
        # den Umgebungs-Radiobutton würde self._gdwh_base_url sonst SOFORT
        # ändern, auch mitten in einer laufenden Löschung. GDS-Key ebenso
        # sperren, da eine laufende Löschung an einen fest eingefrorenen
        # GDS-Key gebunden ist (siehe _gdwh_start_deletion).
        state = "disabled" if locked else "normal"
        for rb in self._gdwh_env_radios:
            rb.config(state=state)
        self._gdwh_gds_combo.config(
            state="disabled" if locked else "readonly")

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else ("normal" if self._auth else "disabled")
        self._load_btn.config(state=state)
        if busy:
            self._start_load_spinner()
        else:
            self._stop_load_spinner()

    def _start_load_spinner(self):
        self._spinner_idx = 0
        self._animate_load_spinner()

    def _animate_load_spinner(self):
        frame = self._SPINNER_FRAMES[self._spinner_idx % len(self._SPINNER_FRAMES)]
        self._load_btn.config(text=f"{frame}  Lade Items …")
        self._spinner_idx += 1
        self._spinner_job = self.after(120, self._animate_load_spinner)

    def _stop_load_spinner(self):
        if self._spinner_job is not None:
            self.after_cancel(self._spinner_job)
            self._spinner_job = None
        self._load_btn.config(text=self._current_load_btn_label())

    def _current_load_btn_label(self) -> str:
        return (self._LOAD_BTN_LABEL_RELOAD if self._items_loaded_once
                else self._LOAD_BTN_LABEL)

    def _log_write(self, text: str):
        def _do():
            self._log.configure(state="normal")
            self._log.insert("end", text)
            self._log.see("end")
            self._log.configure(state="disabled")
        self.after(0, _do)

    def _gdwh_log_write(self, text: str):
        def _do():
            self._gdwh_log.configure(state="normal")
            self._gdwh_log.insert("end", text)
            self._gdwh_log.see("end")
            self._gdwh_log.configure(state="disabled")
        self.after(0, _do)


# ─── Einstiegspunkt ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    app = KryDeleteApp()
    app.mainloop()
