"""Outils communs aux scrapers : session HTTP, parsing, extraction générique des photos."""

import json
import logging
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import config

log = logging.getLogger(__name__)

EXTENSIONS_IMAGE = (".jpg", ".jpeg", ".png", ".webp")


def session_http() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    })
    return s


def _get(session: requests.Session, url: str, delai: float, **kwargs) -> requests.Response | None:
    """GET avec pause, et un nouvel essai après un 429 (trop de requêtes) ou une erreur 5xx."""
    for essai in (1, 2):
        try:
            r = session.get(url, timeout=config.SCRAPER_TIMEOUT, **kwargs)
            r.raise_for_status()
            time.sleep(delai)
            return r
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else None
            if essai == 1 and code == 429:
                log.warning("Trop de requêtes vers %s : pause de %.0fs", url.split("/")[2], config.SCRAPER_ATTENTE_429)
                time.sleep(config.SCRAPER_ATTENTE_429)
                continue
            if essai == 1 and code and code >= 500:
                time.sleep(delai * 2)
                continue
            log.warning("Échec GET %s : %s", url, exc)
            return None
        except requests.RequestException as exc:
            log.warning("Échec GET %s : %s", url, exc)
            return None
    return None


def get_html(session: requests.Session, url: str, delai: float | None = None, **kwargs) -> BeautifulSoup | None:
    r = _get(session, url, config.SCRAPER_DELAI if delai is None else delai, **kwargs)
    return BeautifulSoup(r.text, "html.parser") if r is not None else None


def get_json(session: requests.Session, url: str, **kwargs) -> dict | list | None:
    r = _get(session, url, config.SCRAPER_DELAI, **kwargs)
    if r is None:
        return None
    try:
        return r.json()
    except ValueError as exc:
        log.warning("Réponse non JSON de %s : %s", url, exc)
        return None


def est_url_annonce(url: str, domaine: str, motif: str) -> bool:
    """Vrai si l'URL est bien une page d'annonce du site (et pas un lien promotionnel ou externe)."""
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
    except ValueError:
        return False
    return p.netloc.endswith(domaine) and re.search(motif, p.path) is not None


def nombre(texte) -> float | None:
    """'1 250 €' -> 1250.0 ; '45,5 m²' -> 45.5 ; None si rien à extraire."""
    if texte is None:
        return None
    if isinstance(texte, (int, float)):
        return float(texte)
    t = str(texte).replace(" ", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", t)
    return float(m.group()) if m else None


def entier(texte) -> int | None:
    n = nombre(texte)
    return int(n) if n is not None else None


def limiter_photos(urls, maximum: int | None = None) -> list[str]:
    """Déduplique, garde l'ordre, ne conserve que des URLs http(s) et limite le nombre."""
    maximum = maximum or config.MAX_PHOTOS_PAR_ANNONCE
    vues: set[str] = set()
    resultat: list[str] = []
    for u in urls or []:
        if not u or not isinstance(u, str):
            continue
        u = u.strip()
        if u.startswith("//"):
            u = "https:" + u
        if not u.startswith("http"):
            continue
        if u in vues:
            continue
        vues.add(u)
        resultat.append(u)
        if len(resultat) >= maximum:
            break
    return resultat


def _urls_images_dans(obj, acc: list[str]) -> None:
    """Parcourt récursivement un objet JSON et collecte les URLs qui ressemblent à des images."""
    if isinstance(obj, str):
        if obj.startswith(("http", "//")) and obj.lower().split("?")[0].endswith(EXTENSIONS_IMAGE):
            acc.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _urls_images_dans(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _urls_images_dans(v, acc)


def extraire_photos_generique(soup: BeautifulSoup, page_url: str,
                              maximum: int | None = None) -> list[str]:
    """Extraction "meilleur effort" des photos d'une page d'annonce.

    Ordre de priorité : JSON-LD (champ image), balises <img>/<source> des galeries, og:image.
    """
    candidats: list[str] = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            donnees = json.loads(script.string or "")
        except ValueError:
            continue
        _urls_images_dans(donnees, candidats)

    for balise in soup.select("img, source"):
        for attr in ("data-src", "data-lazy", "data-original", "src", "data-srcset", "srcset"):
            valeur = balise.get(attr)
            if not valeur:
                continue
            premier = str(valeur).split(",")[0].split()[0]
            if premier.lower().split("?")[0].endswith(EXTENSIONS_IMAGE):
                candidats.append(urljoin(page_url, premier))

    for meta in soup.select('meta[property="og:image"]'):
        if meta.get("content"):
            candidats.append(urljoin(page_url, meta["content"]))

    # On écarte les logos / icônes / avatars évidents.
    filtres = [c for c in candidats if not re.search(r"logo|icon|avatar|sprite|placeholder", c, re.I)]
    return limiter_photos(filtres, maximum)


def code_postal_dans(texte) -> str | None:
    """Extrait un code postal français (5 chiffres) d'un texte libre, ex. « Paris 12e (75012) »."""
    if not texte:
        return None
    m = re.search(r"\b(\d{5})\b", str(texte))
    return m.group(1) if m else None


def nettoyer_ville(texte) -> str:
    """« Paris 12e (75012) » -> « Paris 12e » ; « Montreuil (93100) » -> « Montreuil »."""
    if not texte:
        return ""
    return re.sub(r"\s*\(?\b\d{5}\b\)?", "", str(texte)).strip(" ,-")


def normaliser_annonce(source: str, titre, ville, prix, surface, pieces, url, photos,
                       code_postal=None, adresse=None) -> dict:
    return {
        "source": source,
        "titre": (titre or "").strip(),
        "ville": nettoyer_ville(ville),
        "code_postal": (str(code_postal).strip() if code_postal else None) or code_postal_dans(ville),
        "adresse": (adresse or "").strip() or None,
        "prix": nombre(prix),
        "surface": nombre(surface),
        "pieces": entier(pieces),
        "url": url,
        "photos": limiter_photos(photos),
    }
