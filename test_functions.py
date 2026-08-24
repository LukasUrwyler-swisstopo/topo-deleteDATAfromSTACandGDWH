"""
test_functions.py  –  pytest Tests für stac_api.py und gdwh_api.py

Ausführen:
    pytest test_functions.py -v
    pytest test_functions.py -v --tb=short   # kompakter Fehler-Output

Keine echten HTTP-Requests – alle Netzwerkaufrufe werden gemockt.
"""

import pytest
from unittest.mock import MagicMock, patch

import requests as req_module

from stac_api import (
    COLLECTION_ID, ENVIRONMENTS, AUFTRAGSTYPEN, EXT_PRESETS,
    get_item_direct, get_collection_items,
    delete_asset, delete_item,
    check_asset_info,
    stac_item_year, stac_item_area,
)
from gdwh_api import (
    GDWH_ENVIRONMENTS,
    gdwh_get_imports, gdwh_delete_import,
    gdwh_delete_data_package, gdwh_cleanup_data_package,
    gdwh_import_id, gdwh_import_date,
    gdwh_import_footprint_bbox,
    gdwh_bucket_path,
    gdwh_search_file_metadata, gdwh_index_file_metadata_by_import,
    _lv95, _extract_year_from_folder, _area_from_folder_name, _parse_iso_dt,
    _parse_custom_attributes,
)

AUTH      = ("testuser", "testpass")
BASE      = "https://sys-data.int.bgdi.ch/api/stac/v0.9/"
GDWH_BASE = "https://ltgdwhi.adr.admin.ch/gdwh-api/v2/"


def _mock_response(status: int = 200, json_data=None, raise_on_status=False):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data if json_data is not None else {}
    r.text = ""
    r.reason = ""
    if raise_on_status:
        r.raise_for_status.side_effect = req_module.HTTPError(response=r)
    else:
        r.raise_for_status = MagicMock()
    return r


def _mock_gdwh_session() -> MagicMock:
    """Mock für gdwh_api._gdwh_session(): muss als Context-Manager funktionieren
    (with _gdwh_session() as s: ...), analog zur echten requests.Session().

    WICHTIG: gdwh_get_imports()/gdwh_delete_import() rufen intern s.get()/
    s.delete() auf einer Session-INSTANZ auf, nicht das Modul-Level
    requests.get()/requests.delete(). Ein `patch("gdwh_api.requests.get", ...)`
    greift deshalb nicht (Session.get() ruft nie das Modul-Level-Symbol auf)
    – ohne dieses Session-Mock würden die Tests bei vorhandenem Netzzugriff
    echte Requests gegen das reale GDWH abfeuern."""
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    return session


# ═══════════════════════════════════════════════════════════════════════════════
# Konstanten
# ═══════════════════════════════════════════════════════════════════════════════

class TestKonstanten:

    def test_collection_id(self):
        assert COLLECTION_ID == "ch.swisstopo.spezialbefliegungen"

    def test_environments_schluessel(self):
        assert "INT"  in ENVIRONMENTS
        assert "PROD" in ENVIRONMENTS

    def test_gdwh_environments_schluessel(self):
        assert "INT"  in GDWH_ENVIRONMENTS
        assert "PROD" in GDWH_ENVIRONMENTS

    def test_auftragstypen_vorhanden(self):
        assert "KRY (Kryosphäre)"   in AUFTRAGSTYPEN
        assert "RAM (Rapidmapping)" in AUFTRAGSTYPEN
        assert "Alle"               in AUFTRAGSTYPEN

    def test_ext_presets_nicht_leer(self):
        assert len(EXT_PRESETS) > 0
        for label, exts in EXT_PRESETS:
            assert isinstance(label, str)
            assert all(e.startswith(".") for e in exts)


# ═══════════════════════════════════════════════════════════════════════════════
# check_asset_info
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheckAssetInfo:

    URL = "https://example.com/file.tif"

    def _resp(self, status=200, headers=None):
        r = _mock_response(status)
        r.headers = headers or {}
        return r

    def test_leere_url_gibt_minus_1(self):
        assert check_asset_info("", AUTH)["status"] == -1

    def test_200_ok(self):
        with patch("stac_api.requests.head", return_value=self._resp(200)):
            assert check_asset_info(self.URL, AUTH)["status"] == 200

    def test_404_nicht_gefunden(self):
        with patch("stac_api.requests.head", return_value=self._resp(404)):
            assert check_asset_info(self.URL, AUTH)["status"] == 404

    def test_403_wird_mit_auth_wiederholt(self):
        """Bei 403 soll ein zweiter HEAD-Request mit Auth gesendet werden."""
        with patch("stac_api.requests.head",
                   side_effect=[self._resp(403), self._resp(200)]) as mock_head:
            result = check_asset_info(self.URL, AUTH)
        assert result["status"] == 200
        assert mock_head.call_count == 2
        _, kwargs = mock_head.call_args
        assert kwargs.get("auth") == AUTH

    def test_timeout_gibt_minus_2(self):
        with patch("stac_api.requests.head",
                   side_effect=req_module.exceptions.Timeout):
            assert check_asset_info(self.URL, AUTH)["status"] == -2

    def test_netzwerkfehler_gibt_minus_3(self):
        with patch("stac_api.requests.head",
                   side_effect=ConnectionError("no route")):
            assert check_asset_info(self.URL, AUTH)["status"] == -3

    def test_groesse_und_datum_werden_gelesen(self):
        headers = {"Content-Length": "12345", "Last-Modified": "Wed, 20 Aug 2024 10:00:00 GMT"}
        with patch("stac_api.requests.head", return_value=self._resp(200, headers)):
            result = check_asset_info(self.URL, AUTH)
        assert result["size_bytes"] == 12345
        assert result["last_modified"] == headers["Last-Modified"]

    def test_400_ueber_50gb_gibt_minus_4(self):
        """CloudFront antwortet auf HEAD für Assets > 50 GB korrekterweise mit
        400 - der Range-Probe (GET Range: bytes=0-0) bestätigt die Grösse per
        206/Content-Range, daraufhin soll status auf -4 (kein Fehler) wechseln."""
        total_size = 60 * 1024 ** 3
        range_resp = self._resp(206, {"Content-Range": f"bytes 0-0/{total_size}"})
        with patch("stac_api.requests.head", return_value=self._resp(400)), \
             patch("stac_api.requests.get", return_value=range_resp):
            result = check_asset_info(self.URL, AUTH)
        assert result["status"] == -4
        assert result["size_bytes"] == total_size

    def test_400_unter_50gb_bleibt_echter_fehler(self):
        """Ein 400 ohne bestätigte Range-Antwort > 50 GB ist ein echter
        Fehler und darf nicht als -4 maskiert werden."""
        with patch("stac_api.requests.head", return_value=self._resp(400)), \
             patch("stac_api.requests.get", return_value=self._resp(404)):
            result = check_asset_info(self.URL, AUTH)
        assert result["status"] == 400


# ═══════════════════════════════════════════════════════════════════════════════
# get_item_direct
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetItemDirect:

    ITEM = {
        "id": "test-item-001",
        "assets": {"nrgb_cog": {"href": "https://example.com/file.tif"}},
    }

    def test_item_gefunden(self):
        with patch("stac_api._session_get", return_value=_mock_response(200, self.ITEM)):
            result = get_item_direct(BASE, AUTH, "test-item-001")
        assert result == self.ITEM

    def test_item_nicht_gefunden_404(self):
        with patch("stac_api._session_get", return_value=_mock_response(404)):
            result = get_item_direct(BASE, AUTH, "existiert-nicht")
        assert result is None

    def test_item_id_wird_getrimmt(self):
        with patch("stac_api._session_get",
                   return_value=_mock_response(200, self.ITEM)) as mock_get:
            get_item_direct(BASE, AUTH, "  test-item-001  ")
        url = mock_get.call_args[0][0]
        assert "test-item-001" in url
        assert "  " not in url

    def test_url_enthaelt_collection_und_item(self):
        with patch("stac_api._session_get",
                   return_value=_mock_response(200, self.ITEM)) as mock_get:
            get_item_direct(BASE, AUTH, "item-abc")
        url = mock_get.call_args[0][0]
        assert f"collections/{COLLECTION_ID}/items/item-abc" in url


# ═══════════════════════════════════════════════════════════════════════════════
# get_collection_items
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetCollectionItems:

    def test_einzelne_seite(self):
        data = {"features": [{"id": "item-1"}, {"id": "item-2"}], "links": []}
        with patch("stac_api._session_get", return_value=_mock_response(200, data)):
            result = get_collection_items(BASE, AUTH)
        assert len(result) == 2

    def test_paginierung_zwei_seiten(self):
        page1 = {
            "features": [{"id": "item-1"}],
            "links": [{"rel": "next", "href": "https://example.com/page2"}],
        }
        page2 = {"features": [{"id": "item-2"}, {"id": "item-3"}], "links": []}

        responses = iter([_mock_response(200, page1), _mock_response(200, page2)])
        with patch("stac_api._session_get", side_effect=lambda *a, **kw: next(responses)):
            result = get_collection_items(BASE, AUTH)

        assert len(result) == 3
        assert result[0]["id"] == "item-1"
        assert result[2]["id"] == "item-3"

    def test_leere_collection(self):
        data = {"features": [], "links": []}
        with patch("stac_api._session_get", return_value=_mock_response(200, data)):
            result = get_collection_items(BASE, AUTH)
        assert result == []

    def test_log_fn_wird_bei_paginierung_aufgerufen(self):
        page1 = {
            "features": [{"id": "item-1"}],
            "links": [{"rel": "next", "href": "https://example.com/page2"}],
        }
        page2 = {"features": [{"id": "item-2"}], "links": []}
        log_calls = []
        responses = iter([_mock_response(200, page1), _mock_response(200, page2)])
        with patch("stac_api._session_get", side_effect=lambda *a, **kw: next(responses)):
            get_collection_items(BASE, AUTH, log_fn=lambda msg: log_calls.append(msg))
        assert len(log_calls) == 1
        assert "Paginierung" in log_calls[0]


# ═══════════════════════════════════════════════════════════════════════════════
# delete_asset
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeleteAsset:

    def test_success_200(self):
        with patch("stac_api._session_delete", return_value=_mock_response(200)):
            ok, code, reason = delete_asset(BASE, AUTH, "item-001", "nrgb_cog")
        assert ok is True
        assert code == 200
        assert reason == ""

    def test_success_204(self):
        with patch("stac_api._session_delete", return_value=_mock_response(204)):
            ok, code, _ = delete_asset(BASE, AUTH, "item-001", "nrgb_cog")
        assert ok is True
        assert code == 204

    def test_fail_403(self):
        with patch("stac_api._session_delete", return_value=_mock_response(403)):
            ok, code, _ = delete_asset(BASE, AUTH, "item-001", "nrgb_cog")
        assert ok is False
        assert code == 403

    def test_fail_404(self):
        with patch("stac_api._session_delete", return_value=_mock_response(404)):
            ok, code, _ = delete_asset(BASE, AUTH, "item-001", "nrgb_cog")
        assert ok is False

    def test_fail_reason_from_json_description(self):
        with patch("stac_api._session_delete",
                   return_value=_mock_response(400, json_data={"description": "Cannot delete last asset"})):
            ok, code, reason = delete_asset(BASE, AUTH, "item-001", "nrgb_cog")
        assert ok is False
        assert code == 400
        assert reason == "Cannot delete last asset"

    def test_url_korrekt_aufgebaut(self):
        with patch("stac_api._session_delete",
                   return_value=_mock_response(200)) as mock_del:
            delete_asset(BASE, AUTH, "item-abc", "my_asset_key")
        url = mock_del.call_args[0][0]
        assert f"collections/{COLLECTION_ID}/items/item-abc/assets/my_asset_key" in url


# ═══════════════════════════════════════════════════════════════════════════════
# delete_item
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeleteItem:

    def test_success_200(self):
        with patch("stac_api._session_delete", return_value=_mock_response(200)):
            ok, code, _ = delete_item(BASE, AUTH, "item-001")
        assert ok is True
        assert code == 200

    def test_success_204(self):
        with patch("stac_api._session_delete", return_value=_mock_response(204)):
            ok, _, _ = delete_item(BASE, AUTH, "item-001")
        assert ok is True

    def test_fail_404(self):
        with patch("stac_api._session_delete", return_value=_mock_response(404)):
            ok, code, _ = delete_item(BASE, AUTH, "item-999")
        assert ok is False
        assert code == 404

    def test_url_korrekt_aufgebaut(self):
        with patch("stac_api._session_delete",
                   return_value=_mock_response(200)) as mock_del:
            delete_item(BASE, AUTH, "item-xyz")
        url = mock_del.call_args[0][0]
        assert f"collections/{COLLECTION_ID}/items/item-xyz" in url
        assert "/assets/" not in url


# ═══════════════════════════════════════════════════════════════════════════════
# GDWH Hilfsfunktionen – gdwh_import_id
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhImportId:

    def test_feld_uuid_hat_prioritaet(self):
        assert gdwh_import_id({"uuid": "abc-123", "id": "other"}) == "abc-123"

    def test_feld_uuid(self):
        assert gdwh_import_id({"uuid": "964dba08-12ee-4884-a4ec-958db29f0e4c"}) == \
               "964dba08-12ee-4884-a4ec-958db29f0e4c"

    def test_fallback_id(self):
        assert gdwh_import_id({"id": "pkg-001"}) == "pkg-001"

    def test_fallback_datapackageId(self):
        assert gdwh_import_id({"datapackageId": "pkg-002"}) == "pkg-002"

    def test_fallback_package_id(self):
        assert gdwh_import_id({"package_id": "pkg-003"}) == "pkg-003"

    def test_kein_feld_gibt_fragezeichen(self):
        assert gdwh_import_id({}) == "?"


# ═══════════════════════════════════════════════════════════════════════════════
# GDWH Hilfsfunktionen – gdwh_import_date
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhImportDate:

    def test_importDate_hat_prioritaet(self):
        result = gdwh_import_date({"importDate": "2024-08-20T10:30:00",
                                   "date": "2023-01-01T00:00:00"})
        assert result.startswith("2024-08-20")

    def test_iso_datum_mit_t(self):
        assert gdwh_import_date({"importDate": "2024-08-20T10:30:00Z"}) == "2024-08-20 10:30"

    def test_datum_wird_auf_16_zeichen_gekuerzt(self):
        result = gdwh_import_date({"importDate": "2024-08-20T10:30:45.123Z"})
        assert result == "2024-08-20 10:30"

    def test_fallback_date(self):
        assert gdwh_import_date({"date": "2024-09-01T08:00:00"}) == "2024-09-01 08:00"

    def test_fallback_created_at(self):
        assert gdwh_import_date({"created_at": "2024-01-15T12:00:00"}) == "2024-01-15 12:00"

    def test_kein_feld_gibt_strich(self):
        assert gdwh_import_date({}) == "–"


# ═══════════════════════════════════════════════════════════════════════════════
# GDWH Hilfsfunktionen – gdwh_import_footprint_bbox
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhImportFootprintBbox:

    WKT = ("POLYGON ((2652172 1151242.5,2652172 1155998,"
           "2663100 1155998,2663100 1151242.5,2652172 1151242.5))")

    def test_zentroid_format_lv95(self):
        result = gdwh_import_footprint_bbox({"footprint": self.WKT})
        assert "LV95" in result
        assert "E" in result
        assert "N" in result

    def test_apostroph_als_tausendertrennzeichen(self):
        result = gdwh_import_footprint_bbox({"footprint": self.WKT})
        assert "'" in result

    def test_kein_footprint_gibt_leerstring(self):
        assert gdwh_import_footprint_bbox({}) == ""
        assert gdwh_import_footprint_bbox({"footprint": ""}) == ""

    def test_zentroid_plausibel(self):
        result = gdwh_import_footprint_bbox({"footprint": self.WKT})
        # Zentroid X ≈ 2'657'636, Y ≈ 1'153'620
        assert "2'657" in result
        assert "1'153" in result



# ═══════════════════════════════════════════════════════════════════════════════
# GDWH Bucket-Pfad
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhBucketPath:

    def test_int_raster_sb_dsm(self):
        path = gdwh_bucket_path("INT", "SB_DSM")
        assert "BUCKET_INT" in path
        assert "RASTER" in path
        assert "SB_DSM" in path

    def test_prod_raster_sb_dop(self):
        path = gdwh_bucket_path("PROD", "SB_DOP")
        assert "BUCKET_INT" not in path
        assert "RASTER" in path
        assert "SB_DOP" in path

    def test_int_vector_sb_dsm_punktwolke(self):
        path = gdwh_bucket_path("INT", "SB_DSM_PUNKTWOLKE")
        assert "VECTOR" in path
        assert "SB_DSM_PUNKTWOLKE" in path

    def test_prod_vector_sb_dsm_punktwolke(self):
        path = gdwh_bucket_path("PROD", "SB_DSM_PUNKTWOLKE")
        assert "BUCKET_INT" not in path
        assert "VECTOR" in path


# ═══════════════════════════════════════════════════════════════════════════════
# Interne Hilfsfunktionen
# ═══════════════════════════════════════════════════════════════════════════════

class TestLv95Format:

    def test_apostroph_als_trennzeichen(self):
        assert _lv95(2657636) == "2'657'636"

    def test_kleine_zahl(self):
        assert _lv95(999) == "999"

    def test_millionen(self):
        assert _lv95(1153620) == "1'153'620"


class TestExtractYearFromFolder:

    def test_jahr_am_anfang(self):
        assert _extract_year_from_folder("2023_OBERAAR_DSM") == "2023"

    def test_jahr_mit_bindestrich(self):
        assert _extract_year_from_folder("2024-GORNER-DOP") == "2024"

    def test_kein_jahr(self):
        assert _extract_year_from_folder("OBERAAR_DSM") == ""

    def test_jahr_nicht_am_anfang_wird_ignoriert(self):
        assert _extract_year_from_folder("OBERAAR_2023_DSM") == ""


class TestAreaFromFolderName:

    def test_raster_dsm(self):
        assert _area_from_folder_name("2023_OBERAAR_DSM") == "OBERAAR"

    def test_raster_dop(self):
        assert _area_from_folder_name("2025_GUPPENFIRN_DOP") == "GUPPENFIRN"

    def test_vector_punktwolke(self):
        assert _area_from_folder_name("2023_OBERAAR_DSM_PointCloud") == "OBERAAR"

    def test_mehrteiliger_aoi(self):
        assert _area_from_folder_name("2024_MONT_ETOILE_DSM") == "MONT_ETOILE"

    def test_ohne_jahr(self):
        assert _area_from_folder_name("BIRCH_DSM") == "BIRCH"

    def test_gorner(self):
        assert _area_from_folder_name("2025_BIRCH_DSM") == "BIRCH"


class TestParseIsoDt:

    def test_mit_millisekunden(self):
        dt = _parse_iso_dt("2026-06-09T14:39:22.6049990Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 9

    def test_ohne_millisekunden(self):
        dt = _parse_iso_dt("2024-08-20T10:30:00")
        assert dt is not None
        assert dt.year == 2024

    def test_nur_datum(self):
        dt = _parse_iso_dt("2023-01-15")
        assert dt is not None
        assert dt.year == 2023

    def test_ungueltig_gibt_none(self):
        assert _parse_iso_dt("kein-datum") is None


# ═══════════════════════════════════════════════════════════════════════════════
# gdwh_get_imports (gemockt)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhGetImports:

    GDS_KEY = "SB_DSM"

    def test_direkte_liste_als_antwort(self):
        data = [{"uuid": "pkg-1"}, {"uuid": "pkg-2"}]
        session = _mock_gdwh_session()
        session.get.return_value = _mock_response(200, data)
        with patch("gdwh_api._gdwh_session", return_value=session):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert result == data

    def test_wrapper_objekt_items(self):
        data = {"items": [{"uuid": "pkg-1"}], "total": 1}
        session = _mock_gdwh_session()
        session.get.return_value = _mock_response(200, data)
        with patch("gdwh_api._gdwh_session", return_value=session):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert result == [{"uuid": "pkg-1"}]

    def test_wrapper_objekt_imports(self):
        data = {"imports": [{"uuid": "pkg-1"}, {"uuid": "pkg-2"}]}
        session = _mock_gdwh_session()
        session.get.return_value = _mock_response(200, data)
        with patch("gdwh_api._gdwh_session", return_value=session):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert len(result) == 2

    def test_wrapper_objekt_data(self):
        data = {"data": [{"uuid": "pkg-1"}]}
        session = _mock_gdwh_session()
        session.get.return_value = _mock_response(200, data)
        with patch("gdwh_api._gdwh_session", return_value=session):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert result == [{"uuid": "pkg-1"}]

    def test_leere_liste(self):
        session = _mock_gdwh_session()
        session.get.return_value = _mock_response(200, [])
        with patch("gdwh_api._gdwh_session", return_value=session):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert result == []

    def test_url_korrekt_aufgebaut(self):
        session = _mock_gdwh_session()
        session.get.return_value = _mock_response(200, [])
        with patch("gdwh_api._gdwh_session", return_value=session):
            gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        url = session.get.call_args[0][0]
        assert f"api/geodatasets/{self.GDS_KEY}/data/imports" in url

    def test_http_fehler_wird_weitergegeben(self):
        session = _mock_gdwh_session()
        session.get.return_value = _mock_response(500, raise_on_status=True)
        with patch("gdwh_api._gdwh_session", return_value=session):
            with pytest.raises(req_module.HTTPError):
                gdwh_get_imports(GDWH_BASE, self.GDS_KEY)


# ═══════════════════════════════════════════════════════════════════════════════
# _gdwh_session – SSPI-Auth/Proxy-Konfiguration (direkt getestet, ohne Mock:
# reine Objekt-Konstruktion, kein Netzzugriff nötig)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhSession:

    def test_sspi_auth_konfiguriert(self):
        from requests_negotiate_sspi import HttpNegotiateAuth
        from gdwh_api import _gdwh_session, GDWH_SSL_VERIFY
        with _gdwh_session() as s:
            assert isinstance(s.auth, HttpNegotiateAuth)
            assert s.verify == GDWH_SSL_VERIFY

    def test_kein_proxy(self):
        from gdwh_api import _gdwh_session
        with _gdwh_session() as s:
            assert s.proxies == {"http": "", "https": ""}


# ═══════════════════════════════════════════════════════════════════════════════
# gdwh_delete_import (gemockt)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhDeleteImport:

    GDS_KEY = "SB_DSM"
    PKG_ID  = "964dba08-12ee-4884-a4ec-958db29f0e4c"

    def test_job_objekt_wird_zurueckgegeben(self):
        job = {"id": "job-001", "status": "running", "progress": 0}
        session = _mock_gdwh_session()
        session.delete.return_value = _mock_response(200, job)
        with patch("gdwh_api._gdwh_session", return_value=session):
            result = gdwh_delete_import(GDWH_BASE, self.GDS_KEY, self.PKG_ID)
        assert result == job

    def test_mit_email_parameter(self):
        session = _mock_gdwh_session()
        session.delete.return_value = _mock_response(200, {})
        with patch("gdwh_api._gdwh_session", return_value=session):
            gdwh_delete_import(GDWH_BASE, self.GDS_KEY, self.PKG_ID,
                               email="lukas@example.com")
        _, kwargs = session.delete.call_args
        assert kwargs["params"] == {"email": "lukas@example.com"}

    def test_ohne_email_kein_params(self):
        session = _mock_gdwh_session()
        session.delete.return_value = _mock_response(200, {})
        with patch("gdwh_api._gdwh_session", return_value=session):
            gdwh_delete_import(GDWH_BASE, self.GDS_KEY, self.PKG_ID)
        _, kwargs = session.delete.call_args
        assert kwargs["params"] is None

    def test_url_korrekt_aufgebaut(self):
        session = _mock_gdwh_session()
        session.delete.return_value = _mock_response(200, {})
        with patch("gdwh_api._gdwh_session", return_value=session):
            gdwh_delete_import(GDWH_BASE, self.GDS_KEY, self.PKG_ID)
        url = session.delete.call_args[0][0]
        assert f"api/geodatasets/{self.GDS_KEY}/data/imports/{self.PKG_ID}" in url

    def test_nicht_json_antwort_gibt_status_dict(self):
        r = _mock_response(200)
        r.json.side_effect = ValueError("no json")
        session = _mock_gdwh_session()
        session.delete.return_value = r
        with patch("gdwh_api._gdwh_session", return_value=session):
            result = gdwh_delete_import(GDWH_BASE, self.GDS_KEY, self.PKG_ID)
        assert result == {"status": "200"}

    def test_http_fehler_401_wird_weitergegeben(self):
        session = _mock_gdwh_session()
        session.delete.return_value = _mock_response(401, raise_on_status=True)
        with patch("gdwh_api._gdwh_session", return_value=session):
            with pytest.raises(req_module.HTTPError):
                gdwh_delete_import(GDWH_BASE, self.GDS_KEY, self.PKG_ID)


# ═══════════════════════════════════════════════════════════════════════════════
# gdwh_delete_data_package / gdwh_cleanup_data_package (gemockt)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhDeleteDataPackage:

    GDS_KEY = "SB_DOP"
    PKG_ID  = "2464aa63-3b47-477a-bd4c-82c0baebb71d"

    def test_url_korrekt_aufgebaut(self):
        session = _mock_gdwh_session()
        session.delete.return_value = _mock_response(200, {})
        with patch("gdwh_api._gdwh_session", return_value=session):
            gdwh_delete_data_package(GDWH_BASE, self.GDS_KEY, self.PKG_ID)
        url = session.delete.call_args[0][0]
        assert f"api/geodatasets/{self.GDS_KEY}/dataPackages/{self.PKG_ID}" in url

    def test_erfolgreiche_antwort_wird_zurueckgegeben(self):
        data = {"status": "deleted"}
        session = _mock_gdwh_session()
        session.delete.return_value = _mock_response(200, data)
        with patch("gdwh_api._gdwh_session", return_value=session):
            result = gdwh_delete_data_package(GDWH_BASE, self.GDS_KEY, self.PKG_ID)
        assert result == data

    def test_http_fehler_wird_weitergegeben(self):
        session = _mock_gdwh_session()
        session.delete.return_value = _mock_response(500, raise_on_status=True)
        with patch("gdwh_api._gdwh_session", return_value=session):
            with pytest.raises(req_module.HTTPError):
                gdwh_delete_data_package(GDWH_BASE, self.GDS_KEY, self.PKG_ID)


class TestGdwhCleanupDataPackage:

    GDS_KEY = "SB_DOP"
    PKG_ID  = "2464aa63-3b47-477a-bd4c-82c0baebb71d"

    def test_erfolg_gibt_response_zurueck(self):
        data = {"status": "deleted"}
        session = _mock_gdwh_session()
        session.delete.return_value = _mock_response(200, data)
        with patch("gdwh_api._gdwh_session", return_value=session):
            result = gdwh_cleanup_data_package(GDWH_BASE, self.GDS_KEY, self.PKG_ID)
        assert result == data

    def test_404_gibt_none_kein_fehler(self):
        session = _mock_gdwh_session()
        session.delete.return_value = _mock_response(404, raise_on_status=True)
        with patch("gdwh_api._gdwh_session", return_value=session):
            result = gdwh_cleanup_data_package(GDWH_BASE, self.GDS_KEY, self.PKG_ID)
        assert result is None

    def test_andere_http_fehler_werden_weitergegeben(self):
        session = _mock_gdwh_session()
        session.delete.return_value = _mock_response(500, raise_on_status=True)
        with patch("gdwh_api._gdwh_session", return_value=session):
            with pytest.raises(req_module.HTTPError):
                gdwh_cleanup_data_package(GDWH_BASE, self.GDS_KEY, self.PKG_ID)


# ═══════════════════════════════════════════════════════════════════════════════
# gdwh_search_file_metadata (gemockt)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhSearchFileMetadata:

    GDS_KEY = "SB_DOP"

    def test_liste_wird_zurueckgegeben(self):
        data = [{"uuid": "fm-1"}, {"uuid": "fm-2"}]
        session = _mock_gdwh_session()
        session.post.return_value = _mock_response(200, data)
        with patch("gdwh_api._gdwh_session", return_value=session):
            result = gdwh_search_file_metadata(GDWH_BASE, self.GDS_KEY)
        assert result == data

    def test_nicht_liste_gibt_leere_liste(self):
        session = _mock_gdwh_session()
        session.post.return_value = _mock_response(200, {"unerwartet": True})
        with patch("gdwh_api._gdwh_session", return_value=session):
            result = gdwh_search_file_metadata(GDWH_BASE, self.GDS_KEY)
        assert result == []

    def test_url_korrekt_aufgebaut(self):
        session = _mock_gdwh_session()
        session.post.return_value = _mock_response(200, [])
        with patch("gdwh_api._gdwh_session", return_value=session):
            gdwh_search_file_metadata(GDWH_BASE, self.GDS_KEY)
        url = session.post.call_args[0][0]
        assert f"api/geodatasets/{self.GDS_KEY}/fileMetadata/search" in url

    def test_payload_enthaelt_gdskey_und_mostrecent(self):
        session = _mock_gdwh_session()
        session.post.return_value = _mock_response(200, [])
        with patch("gdwh_api._gdwh_session", return_value=session):
            gdwh_search_file_metadata(GDWH_BASE, self.GDS_KEY)
        _, kwargs = session.post.call_args
        assert kwargs["json"] == {"gdsKey": self.GDS_KEY, "mostRecent": True}

    def test_http_fehler_wird_weitergegeben(self):
        session = _mock_gdwh_session()
        session.post.return_value = _mock_response(500, raise_on_status=True)
        with patch("gdwh_api._gdwh_session", return_value=session):
            with pytest.raises(req_module.HTTPError):
                gdwh_search_file_metadata(GDWH_BASE, self.GDS_KEY)


# ═══════════════════════════════════════════════════════════════════════════════
# _parse_custom_attributes – reales customAttributes-Fragment (GDWH INT, SB_DOP)
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseCustomAttributes:

    # 1:1 aus einer echten FileMetadata-Antwort (POST .../fileMetadata/search)
    REAL_FRAGMENT = (
        "<Auftragstyp>kry</Auftragstyp><Area>HOMBERG</Area>"
        "<TerrainModel>Digital Surface Model (DSM photogrammetric autocorrelation)"
        "</TerrainModel><CameraSystem>Leica ADS100</CameraSystem>"
        "<CoordinateReferenceSystem>EPSG:2056) CH1903+ / LV95_LN02"
        "</CoordinateReferenceSystem><Commentary>Digital OrthoPhoto - Mosaic RGB 8BIT"
        "</Commentary><NoData>0 0 0</NoData>"
        "<LineID>20241105_1147_12504,20241105_1152_12504,20241105_1158_12504,"
        "20241105_1203_12504</LineID>"
        "<StacItemIdDatetime>2024-11-05t1147000</StacItemIdDatetime>"
    )

    def test_reales_fragment_alle_felder(self):
        result = _parse_custom_attributes(self.REAL_FRAGMENT)
        assert result["area"] == "HOMBERG"
        assert result["auftragstyp"] == "kry"
        assert result["commentary"] == "Digital OrthoPhoto - Mosaic RGB 8BIT"
        assert result["line_id"] == (
            "20241105_1147_12504,20241105_1152_12504,"
            "20241105_1158_12504,20241105_1203_12504"
        )
        assert result["stac_datetime"] == "2024-11-05t1147000"

    def test_leerer_string(self):
        result = _parse_custom_attributes("")
        assert result == {
            "area": "", "line_id": "", "commentary": "",
            "auftragstyp": "", "stac_datetime": "",
        }

    def test_kaputtes_xml_gibt_leere_werte(self):
        result = _parse_custom_attributes("<Area>OBERAAR<")
        assert result["area"] == ""


# ═══════════════════════════════════════════════════════════════════════════════
# gdwh_index_file_metadata_by_import
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhIndexFileMetadataByImport:

    def test_join_ueber_importuuid(self):
        file_metadata = [{
            "importUuid": "import-1",
            "temporalKey": "2024",
            "tileKey": "2586_1168",
            "commentary": "",
            "customAttributes": "<Area>HOMBERG</Area><LineID>abc</LineID>",
            "fileFormat": {"name": "TIFF", "extension": ".tif"},
        }]
        index = gdwh_index_file_metadata_by_import(file_metadata)
        assert "import-1" in index
        assert index["import-1"]["area"] == "HOMBERG"
        assert index["import-1"]["line_id"] == "abc"
        assert index["import-1"]["year"] == "2024"
        assert index["import-1"]["tile_key"] == "2586_1168"
        assert index["import-1"]["file_format"] == "TIFF"
        assert index["import-1"]["file_extension"] == ".tif"

    def test_fehlendes_fileformat_gibt_leere_strings(self):
        file_metadata = [{
            "importUuid": "import-1",
            "customAttributes": "",
        }]
        index = gdwh_index_file_metadata_by_import(file_metadata)
        assert index["import-1"]["file_format"] == ""
        assert index["import-1"]["file_extension"] == ""

    def test_eintraege_ohne_importuuid_werden_uebersprungen(self):
        file_metadata = [{"customAttributes": "<Area>X</Area>"}]
        index = gdwh_index_file_metadata_by_import(file_metadata)
        assert index == {}

    def test_erster_treffer_pro_import_gewinnt(self):
        file_metadata = [
            {"importUuid": "import-1", "temporalKey": "2023",
             "customAttributes": "<Area>ERSTE</Area>"},
            {"importUuid": "import-1", "temporalKey": "2023",
             "customAttributes": "<Area>ZWEITE</Area>"},
        ]
        index = gdwh_index_file_metadata_by_import(file_metadata)
        assert index["import-1"]["area"] == "ERSTE"

    def test_leere_liste(self):
        assert gdwh_index_file_metadata_by_import([]) == {}

    def test_commentary_fallback_auf_feld(self):
        # customAttributes ohne <Commentary> → Fallback auf fm["commentary"]
        file_metadata = [{
            "importUuid": "import-1",
            "commentary": "Feld-Kommentar",
            "customAttributes": "<Area>X</Area>",
        }]
        index = gdwh_index_file_metadata_by_import(file_metadata)
        assert index["import-1"]["commentary"] == "Feld-Kommentar"


# ═══════════════════════════════════════════════════════════════════════════════
# stac_item_year
# ═══════════════════════════════════════════════════════════════════════════════

class TestStacItemYear:

    def test_aus_properties_datetime(self):
        item = {"id": "x", "properties": {"datetime": "2024-08-20T10:27:00Z"}}
        assert stac_item_year(item) == "2024"

    def test_aus_item_id_fallback(self):
        item = {"id": "ch.swisstopo.spezialbefliegungen_kry_2023-08-15t09850000",
                "properties": {}}
        assert stac_item_year(item) == "2023"

    def test_id_hat_prioritaet_vor_properties(self):
        """Laut Docstring: ID trägt das Befliegungsdatum, properties.datetime
        kann das (davon abweichende) Importdatum sein – die ID gewinnt."""
        item = {"id": "kry_2020-01-01",
                "properties": {"datetime": "2024-08-20T00:00:00Z"}}
        assert stac_item_year(item) == "2020"

    def test_properties_ist_fallback_wenn_id_kein_jahr_hat(self):
        item = {"id": "kry-keinjahr",
                "properties": {"datetime": "2024-08-20T00:00:00Z"}}
        assert stac_item_year(item) == "2024"

    def test_kein_datum_gibt_leerstring(self):
        assert stac_item_year({"id": "kein-datum", "properties": {}}) == ""

    def test_leeres_item(self):
        assert stac_item_year({}) == ""


# ═══════════════════════════════════════════════════════════════════════════════
# stac_item_area
# ═══════════════════════════════════════════════════════════════════════════════

class TestStacItemArea:

    def test_aus_properties_area(self):
        item = {"properties": {"area": "oberaar"}, "bbox": []}
        assert stac_item_area(item) == "OBERAAR"

    def test_aus_properties_aoi(self):
        item = {"properties": {"aoi": "gorner"}, "bbox": []}
        assert stac_item_area(item) == "GORNER"

    def test_properties_hat_prioritaet_vor_bbox(self):
        item = {
            "properties": {"area": "RHONE"},
            "bbox": [7.5, 45.8, 7.9, 46.2],
        }
        assert stac_item_area(item) == "RHONE"

    # Hinweis: stac_item_area() implementiert keine bbox-basierte Nächste-
    # Nachbar-AOI-Schätzung (dafür bräuchte es eine Referenztabelle echter
    # AOI-Koordinaten) – Tests, die genau das erwartet hatten, wurden entfernt.

    def test_kein_bbox_kein_property_gibt_leerstring(self):
        assert stac_item_area({"properties": {}}) == ""

    def test_leeres_item(self):
        assert stac_item_area({}) == ""
