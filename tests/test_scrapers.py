"""Scrapers : validation des URL d'annonces, reprise après 429, complément des nouvelles annonces."""
from unittest.mock import MagicMock, patch

import requests

import config
from scrapers import base, pap


def test_est_url_annonce():
    assert base.est_url_annonce("https://www.pap.fr/annonces/appartement-rungis-94150-r455901431", "pap.fr", pap.MOTIF_ANNONCE)
    assert not base.est_url_annonce("https://www.pap.fr/pass-prioritaire", "pap.fr", pap.MOTIF_ANNONCE)
    assert not base.est_url_annonce("https://www.pap.fr/outils/atelier/detail?slug=x", "pap.fr", pap.MOTIF_ANNONCE)
    assert not base.est_url_annonce("https://www.acceslogement.fr/annonces/location-thiais-r33819", "pap.fr", pap.MOTIF_ANNONCE)


def _reponse(code: int, texte: str = "<html></html>"):
    r = MagicMock(spec=requests.Response)
    r.status_code = code
    r.text = texte
    if code >= 400:
        exc = requests.HTTPError(f"{code} error")
        exc.response = r
        r.raise_for_status.side_effect = exc
    return r


def test_get_html_reprend_apres_429():
    session = MagicMock()
    session.get.side_effect = [_reponse(429), _reponse(200, "<p>ok</p>")]
    with patch.object(base.time, "sleep") as sleep:
        soup = base.get_html(session, "https://www.pap.fr/annonces/x-r1")
    assert soup is not None and soup.get_text() == "ok"
    assert session.get.call_count == 2
    assert any(call.args[0] == config.SCRAPER_ATTENTE_429 for call in sleep.call_args_list)


def test_get_html_abandonne_apres_deux_echecs():
    session = MagicMock()
    session.get.side_effect = [_reponse(429), _reponse(429)]
    with patch.object(base.time, "sleep"):
        assert base.get_html(session, "https://www.pap.fr/annonces/x-r1") is None


def test_pap_ignore_les_blocs_promotionnels():
    from bs4 import BeautifulSoup
    html = """
    <div class="search-list-item"><a class="item-title" href="/annonces/appartement-rungis-94150-r455901431">T2 Rungis</a>
      <span class="item-price">1 000 €</span><ul class="item-tags"><li>2 pièces</li><li>40 m²</li></ul></div>
    <div class="search-list-item"><a class="item-title" href="/pass-prioritaire">Pass prioritaire</a></div>
    <div class="search-list-item"><a class="item-title" href="https://www.acceslogement.fr/annonces/location-r33819">Logement social</a></div>
    """
    cartes = BeautifulSoup(html, "html.parser").select("div.search-list-item")
    annonces = [a for a in (pap._parser_carte(c, "Rungis") for c in cartes) if a]
    assert [a["url"] for a in annonces] == ["https://www.pap.fr/annonces/appartement-rungis-94150-r455901431"]
    assert annonces[0]["prix"] == 1000 and annonces[0]["surface"] == 40 and annonces[0]["pieces"] == 2


def test_completion_seulement_pour_les_nouvelles(tmp_path):
    import db
    import main

    def faux_scraper(_c):
        return [{"source": "pap", "titre": "Ancienne", "ville": "Rungis", "prix": 900.0, "surface": 40.0,
                 "pieces": 2, "url": "https://www.pap.fr/annonces/a-r1", "photos": []},
                {"source": "pap", "titre": "Nouvelle", "ville": "Rungis", "prix": 950.0, "surface": 41.0,
                 "pieces": 2, "url": "https://www.pap.fr/annonces/b-r2", "photos": []},
                {"source": "pap", "titre": "Hors ville", "ville": "Paris", "prix": 950.0, "surface": 41.0,
                 "pieces": 2, "url": "https://www.pap.fr/annonces/c-r3", "photos": []}]

    completees = []

    def faux_completer(session, annonce):
        completees.append(annonce["url"])
        annonce["photos"] = ["https://ex.com/p.jpg"]

    with patch.object(config, "DB_PATH", str(tmp_path / "t.db")), patch.object(config, "DESTINATIONS", []), \
         patch.object(config, "VILLES", ["Rungis"]), \
         patch.dict(main.SCRAPERS, {"pap": faux_scraper}, clear=True), \
         patch.dict(main.COMPLETEURS, {"pap": faux_completer}, clear=True):
        conn = db.init_db()
        db.inserer_annonce(conn, {"source": "pap", "url": "https://www.pap.fr/annonces/a-r1", "photos": []})
        conn.close()
        main.executer("rapide", sources=["pap"])
    assert completees == ["https://www.pap.fr/annonces/b-r2"]      # ni l'ancienne, ni celle hors ville
    conn = db.init_db(str(tmp_path / "t.db"))
    nouvelle = next(a for a in db.lister_annonces(conn) if a["url"].endswith("b-r2"))
    assert nouvelle["photos"] == ["https://ex.com/p.jpg"]


def test_extraction_prix_surface_pieces():
    assert base.extraire_prix("1 250 € / mois") == 1250      # espace insécable étroite (PAP)
    assert base.extraire_prix("1 250 €") == 1250 and base.extraire_prix("950€") == 950
    assert base.extraire_prix("Appartement 1 pièce 25 m²") is None   # pas de montant en euros
    assert base.extraire_prix("1 €") is None                          # implausible
    assert base.extraire_surface("T2 de 45,5 m² lumineux") == 45.5
    assert base.extraire_surface("38 m2") == 38 and base.extraire_surface("5 m²") is None
    assert base.extraire_pieces("2 pièces 38 m²") == 2 and base.extraire_pieces("T3 lumineux") == 3
    assert base.extraire_pieces("3 p. 60 m²") == 3 and base.extraire_pieces("Studio") is None
    a = base.normaliser_annonce("pap", "T2", "Rungis", "1 €", "5 m²", "2", "u", [])
    assert a["prix"] is None and a["surface"] is None


def test_pap_prix_avec_espace_insecable():
    from bs4 import BeautifulSoup
    html = ('<div class="search-list-item"><a class="item-title" href="/annonces/appartement-rungis-94150-r1">'
            'Appartement 1 pièce</a><span class="item-price">1 250 €</span>'
            '<ul class="item-tags"><li>2 pièces</li><li>40,5 m²</li></ul></div>')
    carte = BeautifulSoup(html, "html.parser").select_one("div.search-list-item")
    a = pap._parser_carte(carte, "Rungis")
    assert a["prix"] == 1250 and a["surface"] == 40.5 and a["pieces"] == 2


def test_recalculer_corrige_les_valeurs_implausibles(tmp_path):
    import db
    import main
    with patch.object(config, "DB_PATH", str(tmp_path / "t.db")), patch.object(config, "DESTINATIONS", []):
        conn = db.init_db()
        db.inserer_annonce(conn, {"source": "pap", "url": "https://www.pap.fr/annonces/a-r1", "prix": 1.0,
                                  "surface": 40.0, "ville": "Rungis", "photos": []})
        main.executer("recalculer", conn=conn)
        a = db.lister_annonces(conn)[0]
    assert a["prix"] is None and a["surface"] == 40.0
