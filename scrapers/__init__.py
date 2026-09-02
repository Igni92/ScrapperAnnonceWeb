"""Scrapers d'annonces. Chaque module expose `scraper(criteres) -> list[dict]`.

Chaque annonce est un dict avec au moins :
    source, titre, ville, prix, surface, pieces, url, photos (liste d'URLs, max MAX_PHOTOS_PAR_ANNONCE)
"""
from . import leboncoin, pap, seloger

SCRAPERS = {
    "pap": pap.scraper,
    "leboncoin": leboncoin.scraper,
    "seloger": seloger.scraper,
}
