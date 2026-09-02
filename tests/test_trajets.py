"""Trajets : géocodage, voiture, transports, cache et maximums (HTTP simulé)."""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import config
import db
import trajets

DEST_RUNGIS = {"nom": "Rungis", "adresse": "Rungis", "mode": "transport", "max_minutes": 30}
DEST_PARIS = {"nom": "Paris", "adresse": "Gare de Lyon, Paris", "mode": "voiture", "max_minutes": None}


def _reponses(url, params=None):
    """Simule BAN, OSRM et Transitous selon l'URL appelée."""
    if url == config.GEOCODAGE_URL:
        q = params["q"]
        if "introuvable" in q:
            return {"features": []}
        coords = {"Rungis": [2.3522, 48.7469], "Vincennes": [2.4389, 48.8456]}.get(q.split()[-1], [2.35, 48.85])
        return {"features": [{"geometry": {"coordinates": coords}, "properties": {"label": f"{q} (géocodé)"}}]}
    if "/route/v1/driving/" in url:
        return {"code": "Ok", "routes": [{"duration": 1500, "distance": 12345}]}
    if "/plan" in url:
        return {"itineraries": [{"duration": 2100, "transfers": 1, "legs": [{"mode": "BUS", "routeShortName": "183"}]},
                                {"duration": 1680, "transfers": 2, "legs": [{"routeShortName": "RER B"}]}]}
    raise AssertionError(f"URL inattendue {url}")


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(str(tmp_path / "t.db"))
    yield c
    c.close()


def test_origine_annonce():
    assert trajets.origine_annonce({"ville": "Paris 12e", "code_postal": "75012"}) == "75012 Paris 12e"
    assert trajets.origine_annonce({"ville": "Vincennes", "adresse": "3 rue X"}) == "3 rue X Vincennes"


def test_prochain_depart_jour_de_semaine():
    with patch.object(config, "TRAJET_HEURE_DEPART", "08:30"):
        d = trajets.prochain_depart()
    assert d.tzinfo == timezone.utc
    assert d.astimezone(trajets.PARIS).weekday() < 5
    assert d.astimezone(trajets.PARIS).strftime("%H:%M") == "08:30"


def test_calcul_transport_et_voiture_avec_cache(conn):
    annonce = {"ville": "Vincennes", "code_postal": "94300", "titre": "T2"}
    with patch.object(trajets, "_get_json", side_effect=_reponses) as http:
        t = trajets.calculer_trajets(conn, annonce, [DEST_RUNGIS, DEST_PARIS])
        appels_1 = http.call_count
        # Une seconde annonce dans la même ville : tout vient du cache.
        annonce2 = {"ville": "Vincennes", "code_postal": "94300", "titre": "T3"}
        trajets.calculer_trajets(conn, annonce2, [DEST_RUNGIS, DEST_PARIS])
        assert http.call_count == appels_1

    assert t["Rungis"]["minutes"] == 28 and t["Rungis"]["mode"] == "transport"       # 1680 s, meilleur itinéraire
    assert t["Rungis"]["detail"]["correspondances"] == 2 and t["Rungis"]["detail"]["lignes"] == ["RER B"]
    assert t["Paris"]["minutes"] == 25 and t["Paris"]["detail"]["distance_km"] == 12.3
    assert annonce["trajet_minutes"] == 28                                              # pire destination
    assert annonce2["trajet_minutes"] == 28
    assert trajets.respecte_maximums(annonce) is True


def test_maximum_depasse_et_inconnu(conn):
    with patch.object(trajets, "_get_json", side_effect=_reponses):
        lente = {"ville": "Vincennes", "code_postal": "94300"}
        trajets.calculer_trajets(conn, lente, [{**DEST_RUNGIS, "max_minutes": 20}])
        assert trajets.respecte_maximums(lente) is False

        inconnue = {"ville": "introuvable"}
        trajets.calculer_trajets(conn, inconnue, [DEST_RUNGIS])
    assert inconnue["trajets"]["Rungis"]["minutes"] is None
    assert inconnue["trajet_minutes"] is None
    assert "géolocalisable" in inconnue["trajets"]["Rungis"]["detail"]["erreur"]
    with patch.object(config, "TRAJET_INCONNU_EXCLURE", False):
        assert trajets.respecte_maximums(inconnue) is True
    with patch.object(config, "TRAJET_INCONNU_EXCLURE", True):
        assert trajets.respecte_maximums(inconnue) is False


def test_echec_reseau_non_mis_en_cache(conn):
    annonce = {"ville": "Vincennes"}
    with patch.object(trajets, "_get_json", side_effect=lambda url, params=None:
                      _reponses(url, params) if url == config.GEOCODAGE_URL else None) as http:
        trajets.calculer_trajets(conn, annonce, [DEST_RUNGIS])
        assert annonce["trajets"]["Rungis"]["minutes"] is None
        n = http.call_count
        trajets.calculer_trajets(conn, annonce, [DEST_RUNGIS])
        assert http.call_count > n            # nouvel essai : l'échec n'a pas été mis en cache


def test_transport_a_pied_quand_pas_d_itineraire(conn):
    def reponses(url, params=None):
        if "/plan" in url:
            return {"itineraries": [], "direct": [{"duration": 600}, {"duration": 900}]}
        return _reponses(url, params)
    annonce = {"ville": "Rungis"}
    with patch.object(trajets, "_get_json", side_effect=reponses):
        t = trajets.calculer_trajets(conn, annonce, [DEST_RUNGIS])
    assert t["Rungis"]["minutes"] == 10 and t["Rungis"]["detail"]["a_pied"] is True


def test_sans_destination(conn):
    annonce = {"ville": "Vincennes"}
    with patch.object(trajets, "_get_json", side_effect=AssertionError("aucun appel attendu")):
        assert trajets.calculer_trajets(conn, annonce, []) == {}
    assert annonce["trajet_minutes"] is None


def test_resume_trajets():
    texte = trajets.resume_trajets({"Rungis": {"mode": "transport", "minutes": 28},
                                    "Paris": {"mode": "voiture", "minutes": None}})
    assert texte == "Rungis 28 min 🚇 · Paris ? 🚗"
