"""Scraper SeLoger — locations, via les résultats HTML + JSON-LD des pages d'annonce.

SeLoger applique aussi une protection anti-bot ; en cas de 403, même remarque que pour
Leboncoin (proxy ou navigateur). Les sélecteurs ci-dessous sont à ajuster si le site change.
"""

import json
import logging
from urllib.parse import urlencode, urljoin

from . import base

log = logging.getLogger(__name__)

SOURCE = "seloger"
BASE_URL = "https://www.seloger.com"
URL_RECHERCHE = BASE_URL + "/list.htm"


def _json_ld_annonces(soup) -> list[dict]:
    """Récupère les objets JSON-LD de type Offer/Product/Apartment présents dans la page."""
    objets = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            donnees = json.loads(script.string or "")
        except ValueError:
            continue
        if isinstance(donnees, list):
            objets.extend(d for d in donnees if isinstance(d, dict))
        elif isinstance(donnees, dict):
            objets.append(donnees)
    return objets


def _parser_carte(carte) -> dict | None:
    lien = carte.select_one("a[href*='seloger.com'], a[data-testid*='card-mfe-covering-link'], a")
    if not lien or not lien.get("href"):
        return None
    url = urljoin(BASE_URL, lien["href"].split("?")[0])
    texte = carte.get_text(" ", strip=True)

    titre = (carte.select_one("[data-testid*='title'], h2, h3") or lien).get_text(" ", strip=True)
    prix_el = carte.select_one("[data-testid*='price'], .price, [class*='Price']")
    prix = base.nombre(prix_el.get_text(" ", strip=True)) if prix_el else None

    surface = pieces = None
    for tag in carte.select("li, [data-testid*='tag'], [class*='Tag']"):
        t = tag.get_text(" ", strip=True)
        if "m²" in t:
            surface = base.nombre(t)
        elif "p" in t and ("pièce" in t or t.strip().endswith("p")):
            pieces = base.entier(t)

    ville_el = carte.select_one("[data-testid*='address'], [class*='Address'], address")
    localisation = ville_el.get_text(" ", strip=True) if ville_el else ""
    # SeLoger affiche souvent « Rue X, Ville (75012) » : on sépare l'adresse de la ville.
    adresse, ville = None, localisation
    if "," in localisation:
        adresse, ville = [p.strip() for p in localisation.rsplit(",", 1)]

    photos = [img.get("data-src") or img.get("src") for img in carte.select("img")]
    if prix is None and "€" in texte:
        prix = base.nombre(texte.split("€")[0][-12:])
    return base.normaliser_annonce(SOURCE, titre, ville, prix, surface, pieces, url, photos,
                                   code_postal=base.code_postal_dans(localisation), adresse=adresse)


def _photos_detail(session, url: str) -> list[str]:
    soup = base.get_html(session, url)
    if soup is None:
        return []
    candidats: list[str] = []
    for objet in _json_ld_annonces(soup):
        image = objet.get("image") or objet.get("photo")
        if isinstance(image, str):
            candidats.append(image)
        elif isinstance(image, list):
            candidats.extend(i if isinstance(i, str) else (i.get("url") or i.get("contentUrl") or "")
                             for i in image)
    if candidats:
        return base.limiter_photos(candidats)
    return base.extraire_photos_generique(soup, url)


def scraper(criteres: dict) -> list[dict]:
    session = base.session_http()
    annonces: list[dict] = []
    for ville in criteres.get("villes", []):
        params = {
            "projects": "1",            # location
            "types": "1,2",             # appartement, maison
            "natures": "1,2,4",
            "price": f"NaN/{int(criteres.get('prix_max', 99999))}",
            "surface": f"{int(criteres.get('surface_min', 0))}/NaN",
            "rooms": ",".join(str(p) for p in range(criteres.get("pieces_min", 1), 6)),
            "qsVersion": "1.0",
            "m": "search_refine",
            "searchText": ville,
        }
        soup = base.get_html(session, URL_RECHERCHE + "?" + urlencode(params))
        if soup is None:
            continue
        cartes = soup.select("[data-testid='sl.explore.card-container'], div[class*='Card__Container'], article")
        log.info("SeLoger %s : %d résultats", ville, len(cartes))
        for carte in cartes:
            annonce = _parser_carte(carte)
            if annonce is None or "seloger" not in annonce["url"]:
                continue
            if not annonce["ville"]:
                annonce["ville"] = ville
            detail = _photos_detail(session, annonce["url"])
            if detail:
                annonce["photos"] = base.limiter_photos(detail + annonce["photos"])
            annonces.append(annonce)
    return annonces
