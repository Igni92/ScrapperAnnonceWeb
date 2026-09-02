"""Outils communs aux scrapers : session HTTP, parsing, extraction générique des photos."""

import json
import logging
import random
import re
import threading
import time
from urllib.parse import urljoin, urlparse

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


# Compteurs par site (hôte) pour un même lancement : pages vues, 429 consécutifs, site abandonné.
_etat_sites: dict[str, dict] = {}
_verrou = threading.Lock()


def reinitialiser_compteurs() -> None:
    """À appeler au début de chaque lancement."""
    with _verrou:
        _etat_sites.clear()


def _hote(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def pause(base_s: float) -> None:
    """Pause aléatoire entre base_s et 2 × base_s : un rythme irrégulier, comme une personne."""
    time.sleep(random.uniform(base_s, base_s * 2))


def site_autorise(url: str) -> bool:
    """False si le site a été abandonné pour ce lancement (trop de 429) ou si le quota de pages est atteint."""
    etat = _etat_sites.setdefault(_hote(url), {"pages": 0, "n429": 0, "abandonne": False})
    if etat["abandonne"]:
        return False
    if etat["pages"] >= config.SCRAPER_MAX_PAGES_PAR_SITE:
        if not etat.get("quota_signale"):
            log.warning("%s : quota de %d pages atteint pour ce lancement, on s'arrête là.",
                        _hote(url), config.SCRAPER_MAX_PAGES_PAR_SITE)
            etat["quota_signale"] = True
        return False
    return True


def _get(session: requests.Session, url: str, delai: float, **kwargs) -> requests.Response | None:
    """GET à rythme lent : pause aléatoire, respect de Retry-After, abandon du site après trop de 429."""
    hote = _hote(url)
    with _verrou:
        if not site_autorise(url):
            return None
        etat = _etat_sites[hote]
        etat["pages"] += 1
    for essai in (1, 2):
        try:
            r = session.get(url, timeout=config.SCRAPER_TIMEOUT, **kwargs)
            r.raise_for_status()
            etat["n429"] = 0
            pause(delai)
            return r
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else None
            if code == 429:
                etat["n429"] += 1
                if etat["n429"] >= config.SCRAPER_MAX_429_PAR_SITE:
                    etat["abandonne"] = True
                    log.warning("%s : %d refus « trop de requêtes » : site laissé tranquille jusqu'au prochain "
                                "lancement (attendez au moins une heure).", hote, etat["n429"])
                    return None
                attente = _retry_after(exc.response) or config.SCRAPER_ATTENTE_429
                log.warning("%s : trop de requêtes, pause de %.0fs", hote, attente)
                time.sleep(attente)
                if essai == 1:
                    continue
                return None
            if essai == 1 and code and code >= 500:
                pause(delai * 2)
                continue
            log.warning("Échec GET %s : %s", url, exc)
            return None
        except requests.RequestException as exc:
            log.warning("Échec GET %s : %s", url, exc)
            return None
    return None


def _retry_after(reponse) -> float | None:
    """Délai demandé par le site (en-tête Retry-After, en secondes), borné à 10 minutes."""
    try:
        valeur = float((getattr(reponse, "headers", None) or {}).get("Retry-After", ""))
        return min(max(valeur, 5.0), 600.0)
    except (TypeError, ValueError, AttributeError):
        return None


def get_html(session: requests.Session, url: str, delai: float | None = None, **kwargs) -> BeautifulSoup | None:
    r = _get(session, url, config.SCRAPER_DELAI if delai is None else delai, **kwargs)
    return BeautifulSoup(r.text, "html.parser") if r is not None else None


def get_html_site(session: requests.Session, source: str, url: str, attendre: str | None = None,
                  **kwargs) -> BeautifulSoup | None:
    """HTML d'une page : via le navigateur piloté si la source est dans SOURCES_NAVIGATEUR, sinon requests."""
    if source in config.SOURCES_NAVIGATEUR:
        from . import navigateur
        html = navigateur.charger(url, attendre=attendre)
        return BeautifulSoup(html, "html.parser") if html else None
    return get_html(session, url, **kwargs)


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


def _compacter(texte) -> str:
    """Retire tous les espaces (y compris insécables « 1 250 ») et unifie la virgule décimale."""
    return re.sub(r"\s+", "", str(texte)).replace(",", ".")


def nombre(texte) -> float | None:
    """'1 250 €' -> 1250.0 ; '45,5 m²' -> 45.5 ; None si rien à extraire."""
    if texte is None:
        return None
    if isinstance(texte, (int, float)):
        return float(texte)
    m = re.search(r"\d+(?:\.\d+)?", _compacter(texte))
    return float(m.group()) if m else None


def extraire_prix(texte) -> float | None:
    """Premier montant suivi de € dans un texte libre : « 1 250 € / mois » -> 1250. None sinon."""
    if texte is None:
        return None
    m = re.search(r"(\d+(?:\.\d{1,2})?)(?:€|euros?\b)", _compacter(texte), re.I)
    if not m:
        return None
    valeur = float(m.group(1))
    return valeur if valeur >= config.PRIX_MIN_PLAUSIBLE else None


def extraire_surface(texte) -> float | None:
    """« 45,5 m² » -> 45.5 (accepte m2 / m²). None si absent ou implausible."""
    if texte is None:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)m[²2]", _compacter(texte), re.I)
    if not m:
        return None
    valeur = float(m.group(1))
    return valeur if valeur >= config.SURFACE_MIN_PLAUSIBLE else None


def extraire_pieces(texte) -> int | None:
    """« 2 pièces », « 3 p. », « T2 », « F3 » -> nombre de pièces."""
    if texte is None:
        return None
    t = str(texte)
    m = re.search(r"(\d+)\s*(?:pi[èe]ces?|p\.?(?=\s|$))", t, re.I) or re.search(r"\b[TF](\d)\b", t)
    return int(m.group(1)) if m else None


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
    prix_val, surface_val = nombre(prix), nombre(surface)
    if prix_val is not None and prix_val < config.PRIX_MIN_PLAUSIBLE:
        log.warning("Loyer implausible (%s €) ignoré pour %s", prix_val, url)
        prix_val = None
    if surface_val is not None and surface_val < config.SURFACE_MIN_PLAUSIBLE:
        log.warning("Surface implausible (%s m²) ignorée pour %s", surface_val, url)
        surface_val = None
    return {
        "source": source,
        "titre": (titre or "").strip(),
        "ville": nettoyer_ville(ville),
        "code_postal": (str(code_postal).strip() if code_postal else None) or code_postal_dans(ville),
        "adresse": (adresse or "").strip() or None,
        "prix": prix_val,
        "surface": surface_val,
        "pieces": entier(pieces),
        "url": url,
        "photos": limiter_photos(photos),
    }
