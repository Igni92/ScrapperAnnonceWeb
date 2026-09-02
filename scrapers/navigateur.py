"""Chargement de pages par un vrai navigateur (Playwright + Chromium), OPTION DÉSACTIVÉE PAR DÉFAUT.

Leboncoin et SeLoger interdisent l'accès automatisé et le bloquent (DataDome) ; y accéder par un
navigateur piloté peut entraîner une restriction de votre adresse IP ou de votre compte. La voie
recommandée pour ces sites est la source « alertes » (e-mails d'alerte, voir alertes_email.py).

Si vous activez quand même cette option (config.SOURCES_NAVIGATEUR), aucun camouflage n'est
appliqué : la fenêtre est visible et c'est à vous de résoudre une éventuelle vérification.
Profil persistant dans cache/navigateur (cookies conservés).
Installation : pip install playwright  puis  python -m playwright install chromium
"""

import logging
import time
from pathlib import Path

import config

log = logging.getLogger(__name__)

PROFIL = Path(__file__).resolve().parent.parent / "cache" / "navigateur"
MARQUEURS_BLOCAGE = ("datadome", "captcha-delivery", "Enable JavaScript and cookies", "Access denied",
                     "Vérification humaine", "geo.captcha", "Just a moment")

_playwright = None
_contexte = None


def disponible() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _ouvrir():
    global _playwright, _contexte
    if _contexte is not None:
        return _contexte
    from playwright.sync_api import sync_playwright
    PROFIL.mkdir(parents=True, exist_ok=True)
    _playwright = sync_playwright().start()
    _contexte = _playwright.chromium.launch_persistent_context(
        str(PROFIL),
        headless=not config.NAVIGATEUR_VISIBLE,
        locale="fr-FR",
        viewport={"width": 1280, "height": 900},
    )
    return _contexte


def fermer() -> None:
    global _playwright, _contexte
    try:
        if _contexte is not None:
            _contexte.close()
        if _playwright is not None:
            _playwright.stop()
    except Exception:  # fermeture best effort
        pass
    _contexte = _playwright = None


def est_bloquee(html: str) -> bool:
    extrait = (html or "")[:20000]
    return any(m.lower() in extrait.lower() for m in MARQUEURS_BLOCAGE) and "__NEXT_DATA__" not in extrait


def charger(url: str, attendre: str | None = None) -> str | None:
    """Renvoie le HTML de la page une fois chargée (ou None). `attendre` : sélecteur CSS à attendre."""
    if not disponible():
        log.error("Playwright n'est pas installé : exécutez  pip install playwright  puis  "
                  "python -m playwright install chromium  (voir README).")
        return None
    from . import base
    if not base.site_autorise(url):
        return None
    try:
        ctx = _ouvrir()
        page = ctx.new_page()
    except Exception as exc:
        log.error("Impossible de lancer le navigateur : %s (python -m playwright install chromium ?)", exc)
        return None
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=config.SCRAPER_TIMEOUT * 1000)
        if attendre:
            try:
                page.wait_for_selector(attendre, timeout=15000)
            except Exception:
                pass
        html = page.content()
        debut = time.time()
        while est_bloquee(html) and time.time() - debut < config.NAVIGATEUR_ATTENTE_CAPTCHA:
            if config.NAVIGATEUR_VISIBLE:
                log.warning("Page de vérification affichée par %s : résolvez-la dans la fenêtre du navigateur…",
                            url.split("/")[2])
            page.wait_for_timeout(3000)
            html = page.content()
        if est_bloquee(html):
            log.warning("Page bloquée par la protection anti-robot : %s", url)
            return None
        from . import base
        base.pause(config.SCRAPER_DELAI_DETAIL)      # rythme lent, irrégulier, comme une lecture humaine
        return html
    except Exception as exc:
        log.warning("Échec navigateur sur %s : %s", url, exc)
        return None
    finally:
        try:
            page.close()
        except Exception:
            pass
