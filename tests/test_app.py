"""Interface web : pages, actions, paramètres, lancement (sans réseau ni modèle)."""
import json
import time
from unittest.mock import patch

import pytest

import config
import db
import jobs
import main


@pytest.fixture
def client(tmp_path):
    import app as application
    with patch.object(config, "DB_PATH", str(tmp_path / "t.db")), \
         patch.object(config, "SETTINGS_PATH", tmp_path / "settings.json"), \
         patch.object(config, "DESTINATIONS", [{"nom": "Rungis", "adresse": "Rungis", "mode": "transport", "max_minutes": 30}]):
        application.app.config["TESTING"] = True
        conn = db.init_db()
        db.inserer_annonce(conn, {"source": "pap", "titre": "T2 propre", "ville": "Paris", "prix": 1000.0,
                                  "surface": 40.0, "pieces": 2, "url": "https://ex.com/1", "score": 70.0,
                                  "photos": ["https://ex.com/p.jpg"], "code_postal": "75012",
                                  "trajets": {"Rungis": {"mode": "transport", "minutes": 25, "max_minutes": 30,
                                                         "detail": {"correspondances": 1, "lignes": ["RER B"]}}},
                                  "trajet_minutes": 25,
                                  "analyse_photo": {"score_etat": 85, "moisissure_detectee": False,
                                                    "points_negatifs": [], "resume": "Propre.",
                                                    "nb_photos_analysees": 3}})
        db.inserer_annonce(conn, {"source": "seloger", "titre": "T3 humide", "ville": "Montreuil", "prix": 1300.0,
                                  "surface": 55.0, "pieces": 3, "url": "https://ex.com/2", "score": 30.0,
                                  "photos": [], "analyse_photo": {"score_etat": 20, "moisissure_detectee": True,
                                                                  "points_negatifs": ["moisissure"], "resume": "Humide.",
                                                                  "nb_photos_analysees": 2}})
        conn.close()
        with application.app.test_client() as c:
            yield c


def test_liste_et_filtres(client):
    page = client.get("/").get_data(as_text=True)
    assert "T2 propre" in page and "T3 humide" in page and "moisissure" in page
    assert "T3 humide" not in client.get("/?moisissure=0").get_data(as_text=True)
    assert "T2 propre" not in client.get("/?source=seloger").get_data(as_text=True)
    assert "T3 humide" in client.get("/?q=humide").get_data(as_text=True)


def test_detail_et_actions(client):
    page = client.get("/annonce/2").get_data(as_text=True)
    assert "Moisissure ou traces" in page and "Humide." in page
    assert client.get("/annonce/999").status_code == 404

    r = client.post("/annonce/1/favori", headers={"X-Requested-With": "fetch"})
    assert r.get_json()["favori"] is True
    assert "T2 propre" in client.get("/?favoris=1").get_data(as_text=True)

    r = client.post("/annonce/1/masquer", headers={"X-Requested-With": "fetch"})
    assert r.get_json()["masquee"] is True
    assert "T2 propre" not in client.get("/").get_data(as_text=True)
    assert "T2 propre" in client.get("/?masquees=1").get_data(as_text=True)

    client.post("/annonce/1/reanalyser")
    assert "à analyser" in client.get("/?masquees=1").get_data(as_text=True)

    client.post("/annonce/1/notes", data={"notes": "à visiter samedi"})
    assert "à visiter samedi" in client.get("/annonce/1").get_data(as_text=True)

    assert client.post("/annonce/2/supprimer").status_code == 302
    assert client.get("/annonce/2").status_code == 404


def _formulaire(**modifs):
    valeurs = config.exporter()
    form = {}
    for nom, type_, *_ in config.PARAMETRES:
        v = valeurs[nom]
        if type_ == "liste":
            v = ", ".join(v)
        elif type_ == "dict_int":
            v = "\n".join(f"{k}: {val}" for k, val in v.items())
        elif type_ == "bool":
            v = "1" if v else ""
        elif type_ == "destinations":
            form["DEST_nom"] = [d["nom"] for d in v]
            form["DEST_adresse"] = [d["adresse"] for d in v]
            form["DEST_mode"] = [d["mode"] for d in v]
            form["DEST_max"] = ["" if d["max_minutes"] is None else str(d["max_minutes"]) for d in v]
            continue
        form[nom] = v
    form.update(modifs)
    return form


def test_parametres_enregistres(client, tmp_path):
    sauvegarde = config.exporter()
    try:
        assert client.get("/parametres").status_code == 200
        r = client.post("/parametres", data=_formulaire(PRIX_MAX="1200", VILLES="Lyon, Villeurbanne",
                                                        TRAJET_MINUTES="Lyon: 15\nVilleurbanne: 25"))
        assert r.status_code == 302
        assert config.PRIX_MAX == 1200 and config.VILLES == ["Lyon", "Villeurbanne"]
        assert config.TRAJET_MINUTES == {"Lyon": 15, "Villeurbanne": 25}
        assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["PRIX_MAX"] == 1200
    finally:
        config.appliquer(sauvegarde)


def test_parametres_poids_invalides(client):
    sauvegarde = config.exporter()
    try:
        r = client.post("/parametres", data=_formulaire(POIDS_PHOTO="0.5"))
        assert r.status_code == 400 and "somme des poids" in r.get_data(as_text=True)
        assert config.POIDS_PHOTO == sauvegarde["POIDS_PHOTO"]
    finally:
        config.appliquer(sauvegarde)


def test_lancement_et_journal(client):
    def faux_executer(mode, **_kw):
        main.log.info("bonjour depuis %s", mode)
        return {"mode": mode, "brutes": 0, "filtrees": 0, "nouvelles": 0, "analysees": 0, "recalculees": 2}

    with patch.object(main, "executer", side_effect=faux_executer):
        r = client.post("/api/lancer", json={"mode": "recalculer"})
        assert r.status_code == 200 and r.get_json()["ok"]
        for _ in range(50):
            etat = client.get("/api/job").get_json()["job"]
            if etat["statut"] in ("termine", "erreur"):
                break
            time.sleep(0.05)
    assert etat["statut"] == "termine"
    assert any("bonjour depuis recalculer" in l for l in etat["lignes"])
    assert etat["resume"]["recalculees"] == 2
    assert client.post("/api/lancer", json={"mode": "inconnu"}).status_code == 400
    assert client.get("/lancer").status_code == 200


def test_lancement_refuse_si_deja_en_cours(client):
    import threading
    barriere = threading.Event()

    def bloquant(mode, **_kw):
        barriere.wait(5)
        return {"mode": mode, "brutes": 0, "filtrees": 0, "nouvelles": 0, "analysees": 0, "recalculees": 0}

    with patch.object(main, "executer", side_effect=bloquant):
        assert client.post("/api/lancer", json={"mode": "rapide"}).status_code == 200
        assert client.post("/api/lancer", json={"mode": "rapide"}).status_code == 409
        barriere.set()
        for _ in range(50):
            if not jobs.en_cours():
                break
            time.sleep(0.05)
    assert not jobs.en_cours()


def test_liste_affiche_et_filtre_les_trajets(client):
    page = client.get("/").get_data(as_text=True)
    assert "25 min" in page and "Trajet max" in page
    assert "T2 propre" in client.get("/?trajet_max=30").get_data(as_text=True)
    assert "T2 propre" not in client.get("/?trajet_max=20").get_data(as_text=True)
    assert "T3 humide" not in client.get("/?trajet_max=60").get_data(as_text=True)   # trajet inconnu
    detail = client.get("/annonce/1").get_data(as_text=True)
    assert "RER B" in detail and "1 correspondance" in detail and "Rungis" in detail


def test_parametres_destinations(client, tmp_path):
    sauvegarde = config.exporter()
    try:
        form = _formulaire(TRAJET_INCONNU_EXCLURE="1")
        form["DEST_nom"] = ["Travail", "", ""]
        form["DEST_adresse"] = ["Rungis", "Gare de Lyon, Paris", ""]
        form["DEST_mode"] = ["transport", "voiture", "transport"]
        form["DEST_max"] = ["30", "", ""]
        r = client.post("/parametres", data=form)
        assert r.status_code == 302
        assert config.DESTINATIONS == [
            {"nom": "Travail", "adresse": "Rungis", "mode": "transport", "max_minutes": 30},
            {"nom": "Gare de Lyon, Paris", "adresse": "Gare de Lyon, Paris", "mode": "voiture", "max_minutes": None},
        ]
        assert config.TRAJET_INCONNU_EXCLURE is True
        assert "Destinations modifiées" in client.get("/parametres").get_data(as_text=True)

        form = _formulaire(TRAJET_HEURE_DEPART="25:99")
        assert client.post("/parametres", data=form).status_code == 400
        form = _formulaire()
        form["DEST_nom"], form["DEST_adresse"], form["DEST_mode"], form["DEST_max"] = ["X"], ["Rungis"], ["velo"], [""]
        assert client.post("/parametres", data=form).status_code == 400
    finally:
        config.appliquer(sauvegarde)
