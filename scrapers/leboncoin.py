"""Scraper Leboncoin — locations, via le JSON `__NEXT_DATA__` embarqué dans la page de résultats.

Leboncoin est protégé par DataDome : si la page renvoie un 403, il faut passer par un proxy
résidentiel ou une session navigateur (Playwright). Le parsing ci-dessous fonctionne dès
que le HTML est obtenu.
"""

import json
import logging
from urllib.parse import urlencode

import config
from . import base

log = logging.getLogger(__name__)

SOURCE = "leboncoin"
BASE_URL = "https://www.leboncoin.fr"
URL_RECHERCHE = BASE_URL + "/recherche"


def _next_data(soup) -> dict | None:
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return None
    try:
        return json.loads(script.string)
    except ValueError:
        return None


def _trouver_ads(obj) -> list[dict]:
    """Cherche récursivement la liste `ads` dans le JSON Next.js."""
    if isinstance(obj, dict):
        if isinstance(obj.get("ads"), list):
            return obj["ads"]
        for v in obj.values():
            trouve = _trouver_ads(v)
            if trouve:
                return trouve
    elif isinstance(obj, list):
        for v in obj:
            trouve = _trouver_ads(v)
            if trouve:
                return trouve
    return []


def _attribut(ad: dict, cle: str):
    for attr in ad.get("attributes", []):
        if attr.get("key") == cle:
            return attr.get("value")
    return None


def _photos_ad(ad: dict) -> list[str]:
    images = ad.get("images") or {}
    for cle in ("urls_large", "urls", "urls_thumb"):
        if images.get(cle):
            return base.limiter_photos(images[cle])
    return []


def _parser_ad(ad: dict) -> dict | None:
    url = ad.get("url")
    if not url:
        return None
    prix = ad.get("price")
    if isinstance(prix, list):
        prix = prix[0] if prix else None
    if prix is not None and base.nombre(prix) is not None and base.nombre(prix) < config.PRIX_MIN_PLAUSIBLE:
        prix = None
    localisation = ad.get("location") or {}
    return base.normaliser_annonce(
        SOURCE,
        ad.get("subject"),
        localisation.get("city"),
        prix,
        _attribut(ad, "square"),
        _attribut(ad, "rooms"),
        url,
        _photos_ad(ad),
        code_postal=localisation.get("zipcode"),
    )


def scraper(criteres: dict) -> list[dict]:
    session = base.session_http()
    annonces: list[dict] = []
    for ville in criteres.get("villes", []):
        params = {
            "category": "10",              # locations
            "real_estate_type": "1,2",     # maison, appartement
            "text": ville,
            "price": f"min-{int(criteres.get('prix_max', 99999))}",
            "rooms": f"{criteres.get('pieces_min', 1)}-max",
            "square": f"{int(criteres.get('surface_min', 0))}-max",
        }
        soup = base.get_html(session, URL_RECHERCHE + "?" + urlencode(params))
        if soup is None:
            continue
        donnees = _next_data(soup)
        if donnees is None:
            log.warning("Leboncoin %s : JSON __NEXT_DATA__ absent (page bloquée ?)", ville)
            continue
        ads = _trouver_ads(donnees)
        log.info("Leboncoin %s : %d résultats", ville, len(ads))
        for ad in ads:
            annonce = _parser_ad(ad)
            if annonce is None:
                continue
            if not annonce["ville"]:
                annonce["ville"] = ville
            annonces.append(annonce)
    return annonces


def completer(session, annonce: dict) -> None:
    """Les photos sont déjà dans le JSON de la page de résultats : rien à faire."""
