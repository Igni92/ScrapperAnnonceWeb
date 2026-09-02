"""Scrapers d'annonces. Chaque module expose `scraper(criteres) -> list[dict]`.

Chaque annonce est un dict avec au moins :
    source, titre, ville, prix, surface, pieces, url, photos (liste d'URLs, max MAX_PHOTOS_PAR_ANNONCE)
"""
from . import alertes_email, leboncoin, pap, seloger

SCRAPERS = {
    "pap": pap.scraper,
    "alertes": alertes_email.scraper,
    "leboncoin": leboncoin.scraper,
    "seloger": seloger.scraper,
}

# Complément d'une annonce (page détail : photos…), appelé seulement pour les nouvelles annonces
# retenues après filtrage, pour limiter les requêtes vers les sites.
COMPLETEURS = {
    "pap": pap.completer,
    "leboncoin": leboncoin.completer,
    "seloger": seloger.completer,
    "alertes": alertes_email.completer,
}
