"""Communes : géométrie, chargement (réseau simulé), trajets par commune et suggestions."""
from unittest.mock import patch

import pytest

import communes
import config
import db

CARRE = {"type": "Polygon", "coordinates": [[[2.0, 48.0], [2.2, 48.0], [2.2, 48.2], [2.0, 48.2], [2.0, 48.0]]]}
FAUSSES = [
    {"code": "94065", "nom": "Rungis", "dep": "94", "codes_postaux": ["94150"], "lat": 48.75, "lon": 2.35, "geometry": CARRE},
    {"code": "94038", "nom": "L'Haÿ-les-Roses", "dep": "94", "codes_postaux": ["94240"], "lat": 48.78, "lon": 2.34, "geometry": CARRE},
    {"code": "94080", "nom": "Vincennes", "dep": "94", "codes_postaux": ["94300"], "lat": 48.85, "lon": 2.44, "geometry": CARRE},
]
DEST = [{"nom": "Rungis", "adresse": "Rungis", "mode": "transport", "max_minutes": 30}]


def test_centroide_et_nom_court():
    lat, lon = communes.centroide(CARRE)
    assert abs(lat - 48.1) < 1e-9 and abs(lon - 2.1) < 1e-9
    assert communes.centroide({"type": "MultiPolygon", "coordinates": [CARRE["coordinates"]]}) == (lat, lon)
    assert communes.nom_court("Paris 12e Arrondissement") == "Paris 12e"
    assert communes.nom_court("1er Ardt") == "Paris 1er" and communes.nom_court("10ème Ardt") == "Paris 10e"
    assert communes.nom_court("Vincennes") == "Vincennes"


def test_chargement_geo_api_puis_secours(tmp_path):
    geo = {"features": [{"properties": {"code": "94065", "nom": "Rungis", "codesPostaux": ["94150"],
                                        "centre": {"coordinates": [2.35, 48.75]}}, "geometry": CARRE}]}
    with patch.object(communes, "CACHE_DIR", tmp_path), patch.object(communes, "_get", return_value=geo) as get:
        res = communes.charger_departement("94")
        assert res[0]["nom"] == "Rungis" and res[0]["lat"] == 48.75 and res[0]["lon"] == 2.35
        communes.charger_departement("94")            # second appel : cache disque, pas de réseau
        assert get.call_count == 1

    fg = {"features": [{"properties": {"code": "94080", "nom": "Vincennes"}, "geometry": CARRE}]}
    with patch.object(communes, "CACHE_DIR", tmp_path / "b"), \
         patch.object(communes, "_get", side_effect=[None, fg]):     # geo.api KO -> france-geojson
        res = communes.charger_departement("94")
    assert [c["nom"] for c in res] == ["Vincennes"] and res[0]["lat"] == pytest.approx(48.1)

    with patch.object(communes, "CACHE_DIR", tmp_path / "c"), patch.object(communes, "_get", return_value=None):
        assert communes.charger_departement("94") == []


def test_trajets_communes_et_suggestions(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    durees = {"94065": 5, "94038": 18, "94080": 49}

    def fausse_duree(conn_, origine, origine_cle, dest, mode):
        code = origine_cle.split(":")[1]
        return durees[code], {"correspondances": 1}

    with patch.object(communes, "charger_departements", return_value=FAUSSES), \
         patch.object(communes.trajets, "duree", side_effect=fausse_duree):
        n = communes.calculer_trajets_communes(conn, ["94"], DEST)
    assert n == 3
    etats = communes.statuts(conn, ["94"])
    assert etats["94080"]["ok"] is False and etats["94038"]["ok"] is True
    assert etats["94038"]["trajets"]["Rungis"]["minutes"] == 18

    sugg = communes.villes_suggerees(conn, ["94"], exclure=["l'hay-les-roses"])   # comparaison sans accents
    assert [c["nom"] for c in sugg] == ["Rungis"]
    sugg = communes.villes_suggerees(conn, ["94"], exclure=[])
    assert [c["nom"] for c in sugg] == ["Rungis", "L'Haÿ-les-Roses"]

    with patch.object(config, "DB_PATH", str(tmp_path / "t.db")), patch.object(config, "VILLES", ["Vincennes"]), \
         patch.object(config, "DESTINATIONS", DEST), patch.object(config, "SUGGESTIONS_AUTO", True):
        assert communes.villes_pour_scraping() == ["Vincennes", "Rungis", "L'Haÿ-les-Roses"]
    with patch.object(config, "VILLES", ["Vincennes"]), patch.object(config, "SUGGESTIONS_AUTO", False):
        assert communes.villes_pour_scraping() == ["Vincennes"]
