"""Source « alertes » : extraction d'annonces depuis un e-mail d'alerte (HTML simulé) et lecture IMAP simulée."""
from unittest.mock import MagicMock, patch

import config
from scrapers import alertes_email

EMAIL_HTML = """
<html><body>
<h1>Nouvelles annonces pour votre recherche</h1>
<table><tr><td>
  <a href="https://www.leboncoin.fr/ad/locations/2917364512?utm_source=alert&utm_campaign=x">
    <img src="https://img.leboncoin.fr/api/v1/lbcpb1/images/abc.jpg" alt=""></a>
  <a href="https://www.leboncoin.fr/ad/locations/2917364512?utm_source=alert">Appartement 2 pièces 42 m² lumineux</a>
  <div>1 150 € / mois</div><div>Rungis (94150)</div>
</td></tr>
<tr><td>
  <a href="https://www.seloger.com/annonces/locations/appartement/thiais-94/253422991.htm?enterprise=0">T2 refait à neuf</a>
  <span>980 € CC</span> <span>38 m2</span> <span>Thiais</span>
  <img src="https://v.seloger.com/s/width/600/visuels/1/2/3/photo.jpg">
</td></tr>
<tr><td><a href="https://www.leboncoin.fr/mes-alertes">Gérer mes alertes</a> <a href="https://www.leboncoin.fr/ad/locations/1">x</a></td></tr>
</body></html>
"""


def test_extraction_depuis_un_email():
    with patch.object(config, "VILLES", ["Rungis", "Thiais"]):
        annonces = {a["url"]: a for a in alertes_email.extraire_annonces(EMAIL_HTML)}
    assert set(annonces) == {"https://www.leboncoin.fr/ad/locations/2917364512",
                             "https://www.seloger.com/annonces/locations/appartement/thiais-94/253422991.htm",
                             "https://www.leboncoin.fr/ad/locations/1"}
    lbc = annonces["https://www.leboncoin.fr/ad/locations/2917364512"]
    assert lbc["source"] == "leboncoin" and lbc["via"] == "alerte"
    assert lbc["titre"] == "Appartement 2 pièces 42 m² lumineux"
    assert lbc["prix"] == 1150 and lbc["surface"] == 42 and lbc["pieces"] == 2
    assert lbc["ville"] == "Rungis" and lbc["code_postal"] == "94150"
    assert lbc["photos"] == ["https://img.leboncoin.fr/api/v1/lbcpb1/images/abc.jpg"]
    slg = annonces["https://www.seloger.com/annonces/locations/appartement/thiais-94/253422991.htm"]
    assert slg["source"] == "seloger" and slg["prix"] == 980 and slg["surface"] == 38 and slg["ville"] == "Thiais"
    assert slg["pieces"] == 2 and slg["photos"] == ["https://v.seloger.com/s/width/600/visuels/1/2/3/photo.jpg"]


def test_scraper_sans_identifiants():
    with patch.object(config, "ALERTES_IMAP_UTILISATEUR", ""), patch.object(config, "ALERTES_IMAP_MOT_DE_PASSE", ""):
        assert alertes_email.scraper({"villes": ["Rungis"]}) == []


def test_lecture_imap_simulee():
    import email.message
    msg = email.message.EmailMessage()
    msg["From"] = "Leboncoin <noreply@leboncoin.fr>"
    msg["Subject"] = "Nouvelles annonces"
    msg.set_content("version texte")
    msg.add_alternative(EMAIL_HTML, subtype="html")

    boite = MagicMock()
    boite.__enter__.return_value = boite
    boite.search.side_effect = [("OK", [b"12 15"]), ("OK", [b""])]
    boite.fetch.return_value = ("OK", [(b"12 (BODY[] {123}", msg.as_bytes()), b")"])
    with patch.object(alertes_email.imaplib, "IMAP4_SSL", return_value=boite), \
         patch.object(config, "VILLES", ["Rungis", "Thiais"]), \
         patch.object(config, "ALERTES_IMAP_UTILISATEUR", "moi@gmail.com"), \
         patch.object(config, "ALERTES_IMAP_MOT_DE_PASSE", "mdp-app"):
        annonces = alertes_email.scraper({"villes": ["Rungis", "Thiais"]})
    boite.login.assert_called_once_with("moi@gmail.com", "mdp-app")
    assert boite.fetch.call_count == 2                       # deux identifiants, un seul expéditeur non vide
    assert len(annonces) == 3 and all(a["via"] == "alerte" for a in annonces)


def test_leboncoin_direct_ignore_sans_navigateur():
    from scrapers import leboncoin, seloger
    with patch.object(config, "SOURCES_NAVIGATEUR", []):
        assert leboncoin.scraper({"villes": ["Rungis"]}) == []
        assert seloger.scraper({"villes": ["Rungis"]}) == []
