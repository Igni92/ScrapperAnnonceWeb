"""Pipeline complet (scraping simulé + modèle simulé), sans réseau."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import config
import db
import main
import photo_analysis as pa


def _faux_scraper(_criteres):
    return [
        {"source": "pap", "titre": "T2 propre", "ville": "Paris", "prix": 1100.0, "surface": 40.0,
         "pieces": 2, "url": "https://ex.com/propre", "photos": ["https://ex.com/1.jpg", "https://ex.com/2.jpg"]},
        {"source": "pap", "titre": "T2 moisi", "ville": "Paris", "prix": 1100.0, "surface": 40.0,
         "pieces": 2, "url": "https://ex.com/moisi", "photos": ["https://ex.com/3.jpg", "https://ex.com/4.jpg"]},
        {"source": "pap", "titre": "T2 sans photo", "ville": "Paris", "prix": 1100.0, "surface": 40.0,
         "pieces": 2, "url": "https://ex.com/vide", "photos": []},
    ]


def _client_par_titre():
    """Le faux modèle répond selon le titre présent dans le texte de la requête."""
    def create(**kwargs):
        texte = kwargs["messages"][0]["content"][-1]["text"]
        if "moisi" in texte:
            payload = {"score_etat": 20, "moisissure_detectee": True,
                       "points_negatifs": ["moisissure plafond"], "resume": "Moisissure."}
        else:
            payload = {"score_etat": 85, "moisissure_detectee": False, "points_negatifs": [], "resume": "Propre."}
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(payload))])
    client = MagicMock()
    client.messages.create.side_effect = create
    return client


def _lancer(args, client, tmp_path):
    with patch.object(config, "DB_PATH", str(tmp_path / "t.db")), \
         patch.object(config, "PHOTO_BACKEND", "api"), \
         patch.object(config, "DESTINATIONS", []), \
         patch.object(config, "VILLES", ["Paris"]), \
         patch.dict(main.SCRAPERS, {"pap": _faux_scraper}, clear=True), \
         patch.object(pa, "get_client", return_value=client), \
         patch.object(pa, "telecharger_photo", return_value=("image/jpeg", b"AAAA")), \
         patch.object(pa.time, "sleep"):
        assert main.main(args) == 0
    return client


def test_pipeline_analyse_nouvelles_puis_cache(tmp_path):
    client = _lancer(["--sources", "pap"], _client_par_titre(), tmp_path)
    assert client.messages.create.call_count == 2  # l'annonce sans photo n'est pas envoyée

    conn = db.init_db(str(tmp_path / "t.db"))
    annonces = {a["url"]: a for a in db.lister_annonces(conn)}
    assert annonces["https://ex.com/moisi"]["analyse_photo"]["moisissure_detectee"] is True
    assert annonces["https://ex.com/moisi"]["score"] <= config.PLAFOND_SCORE_MOISISSURE
    assert annonces["https://ex.com/propre"]["score"] > annonces["https://ex.com/moisi"]["score"]
    # Sans photo : score neutre mis en cache, pas de pénalité.
    assert annonces["https://ex.com/vide"]["analyse_photo"]["score_etat"] == config.PHOTO_SCORE_NEUTRE

    # Second run : tout est en cache, aucun appel API.
    client2 = _lancer(["--sources", "pap"], _client_par_titre(), tmp_path)
    assert client2.messages.create.call_count == 0


def test_skip_photos_puis_rattrapage(tmp_path):
    client = _lancer(["--sources", "pap", "--skip-photos"], _client_par_titre(), tmp_path)
    assert client.messages.create.call_count == 0
    conn = db.init_db(str(tmp_path / "t.db"))
    assert len(db.annonces_sans_analyse(conn)) == 3

    client = _lancer(["--no-scrape", "--analyser-manquantes"], _client_par_titre(), tmp_path)
    assert client.messages.create.call_count == 2
    assert db.annonces_sans_analyse(conn) == []


def test_erreur_api_non_mise_en_cache(tmp_path):
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="{oops")])
    _lancer(["--sources", "pap"], client, tmp_path)
    conn = db.init_db(str(tmp_path / "t.db"))
    en_attente = {a["url"] for a in db.annonces_sans_analyse(conn)}
    assert en_attente == {"https://ex.com/propre", "https://ex.com/moisi"}


def test_pipeline_ecarte_les_trajets_trop_longs(tmp_path):
    import trajets

    def faux_calcul(conn, annonce, destinations=None):
        minutes = 45 if "moisi" in annonce["url"] else 20
        annonce["trajets"] = {"Rungis": {"mode": "transport", "minutes": minutes, "max_minutes": 30, "detail": {}}}
        annonce["trajet_minutes"] = minutes
        return annonce["trajets"]

    with patch.object(config, "DB_PATH", str(tmp_path / "t.db")), \
         patch.object(config, "VILLES", ["Paris"]), \
         patch.object(config, "DESTINATIONS", [{"nom": "Rungis", "adresse": "Rungis", "mode": "transport", "max_minutes": 30}]), \
         patch.dict(main.SCRAPERS, {"pap": _faux_scraper}, clear=True), \
         patch.object(trajets, "calculer_trajets", side_effect=faux_calcul):
        assert main.main(["--sources", "pap", "--skip-photos"]) == 0
        conn = db.init_db(str(tmp_path / "t.db"))
        urls = {a["url"] for a in db.lister_annonces(conn)}
    assert "https://ex.com/moisi" not in urls and "https://ex.com/propre" in urls
    propre = next(a for a in db.lister_annonces(conn) if a["url"].endswith("propre"))
    assert propre["trajet_minutes"] == 20 and propre["trajets"]["Rungis"]["minutes"] == 20


def test_filtre_villes():
    villes = ["Vincennes", "Paris 13e", "L'Haÿ-les-Roses"]
    assert main.ville_autorisee({"ville": "Vincennes"}, villes)
    assert main.ville_autorisee({"ville": "vincennes", "code_postal": "94300"}, villes)
    assert main.ville_autorisee({"ville": "L'Hay-les-Roses"}, villes)           # accents ignorés
    assert main.ville_autorisee({"ville": "Paris", "code_postal": "75013"}, villes)   # arrondissement déduit
    assert not main.ville_autorisee({"ville": "Paris", "code_postal": "75015"}, villes)
    assert not main.ville_autorisee({"ville": "Paris"}, villes)                 # arrondissement inconnu
    assert not main.ville_autorisee({"ville": "Montreuil"}, villes)
    assert main.ville_autorisee({"ville": ""}, villes)                          # ville inconnue : gardée
    assert main.ville_autorisee({"ville": "Paris 15e"}, ["Paris"])             # « Paris » couvre tout
    assert main.ville_annonce({"ville": "Paris", "code_postal": "75001"}) == "Paris 1er"

    brutes = [{"ville": "Paris", "code_postal": "75015", "prix": 1000.0, "surface": 40.0, "pieces": 2, "url": "u1"},
              {"ville": "Vincennes", "prix": 1000.0, "surface": 40.0, "pieces": 2, "url": "u2"}]
    with patch.object(config, "FILTRER_VILLES", True):
        assert [a["url"] for a in main.filtrer(brutes, villes)] == ["u2"]
    with patch.object(config, "FILTRER_VILLES", False):
        assert len(main.filtrer(brutes, villes)) == 2
