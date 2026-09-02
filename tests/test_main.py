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
