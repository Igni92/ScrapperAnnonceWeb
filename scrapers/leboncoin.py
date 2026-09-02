"""Scraper Leboncoin — locations, via le JSON `__NEXT_DATA__` embarqué dans la page de résultats.

Leboncoin interdit et bloque l'accès automatisé (DataDome) : en accès direct, la page est
refusée et votre adresse IP peut être restreinte. Préférez la source « alertes » (e-mails
d'alerte). Ce module reste utilisable avec l'option navigateur (config.SOURCES_NAVIGATEUR),
à vos risques.
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


def _localisation(ville: str) -> str:
    """Paramètre `locations` de Leboncoin : Ville_CP__lat_lon_rayon (géocodage BAN, avec cache)."""
    try:
        import db
        import trajets
        conn = db.init_db()
        try:
            geo = trajets.geocoder(conn, ville)
        finally:
            conn.close()
    except Exception:
        geo = None
    if not geo:
        return ville
    cp = geo.get("code_postal") or ""
    return f"{ville}_{cp}__{geo['lat']:.5f}_{geo['lon']:.5f}_3000"


def scraper(criteres: dict) -> list[dict]:
    if SOURCE not in config.SOURCES_NAVIGATEUR:
        log.warning("Leboncoin bloque l'accès direct (protection anti-robot) : utilisez la source « alertes » "
                    "(e-mails d'alerte). Source ignorée.")
        return []
    session = base.session_http()
    annonces: list[dict] = []
    for ville in criteres.get("villes", []):
        params = {
            "category": "10",              # locations
            "real_estate_type": "1,2",     # maison, appartement
            "locations": _localisation(ville),
            "price": f"min-{int(criteres.get('prix_max', 99999))}",
            "rooms": f"{criteres.get('pieces_min', 1)}-max",
            "square": f"{int(criteres.get('surface_min', 0))}-max",
        }
        soup = base.get_html_site(session, SOURCE, URL_RECHERCHE + "?" + urlencode(params),
                                  attendre="script#__NEXT_DATA__")
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
