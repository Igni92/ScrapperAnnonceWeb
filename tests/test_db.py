import db


def _annonce(url="https://ex.com/1", analyse=None):
    return {"source": "pap", "titre": "T2 lumineux", "ville": "Paris", "prix": 1000.0,
            "surface": 40.0, "pieces": 2, "url": url, "score": 70.0,
            "photos": ["https://ex.com/p1.jpg"], "analyse_photo": analyse}


def test_insertion_et_cache(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    assert not db.annonce_existe(conn, "https://ex.com/1")
    db.inserer_annonce(conn, _annonce())
    assert db.annonce_existe(conn, "https://ex.com/1")
    assert db.get_analyse_photo(conn, "https://ex.com/1") is None
    assert [a["url"] for a in db.annonces_sans_analyse(conn)] == ["https://ex.com/1"]

    analyse = {"score_etat": 35, "moisissure_detectee": True,
               "points_negatifs": ["traces noires"], "resume": "Humidité", "nb_photos_analysees": 4}
    db.maj_analyse_photo(conn, "https://ex.com/1", analyse, score=30.0)
    cache = db.get_analyse_photo(conn, "https://ex.com/1")
    assert cache["score_etat"] == 35 and cache["moisissure_detectee"] is True
    assert cache["points_negatifs"] == ["traces noires"]
    assert db.annonces_sans_analyse(conn) == []
    assert db.lister_annonces(conn)[0]["score"] == 30.0


def test_insertion_avec_analyse_et_doublon(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    a = _annonce(analyse={"score_etat": 80, "moisissure_detectee": False,
                          "points_negatifs": [], "resume": "OK", "nb_photos_analysees": 3})
    db.inserer_annonce(conn, a)
    db.inserer_annonce(conn, a)  # doublon ignoré
    assert len(db.lister_annonces(conn)) == 1
    assert db.get_analyse_photo(conn, a["url"])["score_etat"] == 80
    assert db.urls_connues(conn, [a["url"], "https://ex.com/autre"]) == {a["url"]}


def test_migration_ajoute_colonnes(tmp_path):
    import sqlite3
    chemin = str(tmp_path / "vieux.db")
    c = sqlite3.connect(chemin)
    c.execute("CREATE TABLE annonces (id INTEGER PRIMARY KEY, source TEXT NOT NULL, titre TEXT, "
              "ville TEXT, prix REAL, surface REAL, pieces INTEGER, url TEXT NOT NULL UNIQUE, "
              "score REAL, cree_le TEXT NOT NULL)")
    c.commit(); c.close()
    conn = db.init_db(chemin)
    colonnes = {r["name"] for r in conn.execute("PRAGMA table_info(annonces)")}
    assert {"score_etat", "moisissure_detectee", "resume_etat", "photo_analyse_le"} <= colonnes
