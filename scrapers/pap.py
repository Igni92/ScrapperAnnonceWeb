"""Scraper PAP (pap.fr) — locations, avec récupération des photos de chaque annonce."""

import logging
import re
from urllib.parse import urljoin

from . import base

log = logging.getLogger(__name__)

SOURCE = "pap"
BASE_URL = "https://www.pap.fr"
# La recherche PAP est encodée dans l'URL ; on requête la page de résultats par ville.
URL_RECHERCHE = BASE_URL + "/annonce/locations-{slug}-g{geo}-a-partir-de-{pieces}-pieces-jusqu-a-{prix}-euros"

# Identifiants géographiques PAP connus. Pour une ville absente, on interroge l'autocomplétion
# du site (best effort : le site est protégé par Cloudflare et peut refuser).
GEO_IDS = {
    "Paris": ("paris-75", "439"),
    "Montreuil": ("montreuil-93100", "37633"),
    "Vincennes": ("vincennes-94300", "37599"),
    "Saint-Mandé": ("saint-mande-94160", "37574"),
}
URL_AUTOCOMPLETE = BASE_URL + "/json/ac-geo"
_geo_cache: dict[str, tuple[str, str] | None] = {}


def _slug(texte: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def resoudre_geo(session, ville: str) -> tuple[str, str] | None:
    """(slug, id) PAP pour une ville : table connue, sinon autocomplétion du site."""
    if ville in GEO_IDS:
        return GEO_IDS[ville]
    if ville in _geo_cache:
        return _geo_cache[ville]
    resultat = None
    donnees = base.get_json(session, URL_AUTOCOMPLETE, params={"q": ville})
    for item in donnees if isinstance(donnees, list) else []:
        nom = str(item.get("name") or item.get("nom") or "")
        ident = item.get("id") or item.get("geo_id")
        if ident and _slug(nom).startswith(_slug(ville)):
            resultat = (_slug(nom), str(ident))
            break
    if resultat is None:
        log.warning("PAP : identifiant géographique introuvable pour %s (ville ignorée). "
                    "Ajoutez-le dans scrapers/pap.py GEO_IDS.", ville)
    _geo_cache[ville] = resultat
    return resultat


def _parser_carte(carte, ville: str) -> dict | None:
    lien = carte.select_one("a.item-title, a[href*='/annonces/']")
    if not lien or not lien.get("href"):
        return None
    url = urljoin(BASE_URL, lien["href"])
    titre = lien.get_text(" ", strip=True)

    prix = base.nombre((carte.select_one(".item-price") or lien).get_text(" ", strip=True))
    surface = pieces = None
    for tag in carte.select(".item-tags li, .item-tags span"):
        texte = tag.get_text(" ", strip=True)
        if "m²" in texte or "m2" in texte:
            surface = base.nombre(texte)
        elif "pièce" in texte:
            pieces = base.entier(texte)

    # Localisation affichée (ex. « Paris 12e (75012) ») : plus précise que la ville recherchée.
    loc = carte.select_one(".item-description strong, .item-location, [class*='location']")
    localisation = loc.get_text(" ", strip=True) if loc else ""
    ville_precise = base.nettoyer_ville(localisation) or ville
    # Photos visibles dès la liste (vignettes) : on complète ensuite avec la page détail.
    photos = [img.get("data-src") or img.get("src") for img in carte.select("img")]
    return base.normaliser_annonce(SOURCE, titre, ville_precise, prix, surface, pieces, url, photos,
                                   code_postal=base.code_postal_dans(localisation) or base.code_postal_dans(titre))


def _photos_detail(session, url: str) -> list[str]:
    soup = base.get_html(session, url)
    if soup is None:
        return []
    candidats = []
    for img in soup.select(".owl-carousel img, .slideshow img, .item-slider img, img[src*='/photo/']"):
        candidats.append(img.get("data-src") or img.get("src"))
    if not candidats:
        return base.extraire_photos_generique(soup, url)
    return base.limiter_photos([urljoin(url, c) for c in candidats if c])


def scraper(criteres: dict) -> list[dict]:
    """Retourne les annonces PAP pour les villes / prix / pièces demandés."""
    session = base.session_http()
    annonces: list[dict] = []
    for ville in criteres.get("villes", []):
        geo_info = resoudre_geo(session, ville)
        if geo_info is None:
            continue
        slug, geo = geo_info
        url = URL_RECHERCHE.format(slug=slug, geo=geo,
                                   pieces=criteres.get("pieces_min", 1),
                                   prix=int(criteres.get("prix_max", 99999)))
        soup = base.get_html(session, url)
        if soup is None:
            continue
        cartes = soup.select("div.search-list-item, div.search-list-item-alt, div[class*='search-list-item']")
        log.info("PAP %s : %d résultats", ville, len(cartes))
        for carte in cartes:
            annonce = _parser_carte(carte, ville)
            if annonce is None:
                continue
            detail = _photos_detail(session, annonce["url"])
            if detail:
                annonce["photos"] = base.limiter_photos(detail + annonce["photos"])
            annonces.append(annonce)
    return annonces
