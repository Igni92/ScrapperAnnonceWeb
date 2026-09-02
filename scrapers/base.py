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


def get_html(session: requests.Session, url: str, **kwargs) -> BeautifulSoup | None:
    try:
        r = session.get(url, timeout=config.SCRAPER_TIMEOUT, **kwargs)
        r.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Échec GET %s : %s", url, exc)
        return None
    time.sleep(config.SCRAPER_DELAI)
    return BeautifulSoup(r.text, "html.parser")


def get_json(session: requests.Session, url: str, **kwargs) -> dict | list | None:
    try:
        r = session.get(url, timeout=config.SCRAPER_TIMEOUT, **kwargs)
        r.raise_for_status()
        donnees = r.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Échec GET JSON %s : %s", url, exc)
        return None
    time.sleep(config.SCRAPER_DELAI)
    return donnees


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


def normaliser_annonce(source: str, titre, ville, prix, surface, pieces, url, photos) -> dict:
    return {
        "source": source,
        "titre": (titre or "").strip(),
        "ville": (ville or "").strip(),
        "prix": nombre(prix),
        "surface": nombre(surface),
        "pieces": entier(pieces),
        "url": url,
        "photos": limiter_photos(photos),
    }
