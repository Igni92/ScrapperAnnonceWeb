"""Source « alertes » : lit les e-mails d'alerte Leboncoin / SeLoger dans votre boîte (IMAP).

Créez une alerte sur chaque site (T2, vos villes, votre budget). Les e-mails reçus sont lus,
et chaque lien d'annonce est extrait avec le texte qui l'entoure (titre, loyer, surface, ville,
photo). Aucune requête n'est envoyée aux sites : rien à bloquer.
"""

import email
import email.header
import imaplib
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import config
from . import base

log = logging.getLogger(__name__)

SOURCE = "alertes"
# Domaines reconnus -> (source attribuée à l'annonce, motif d'URL d'annonce)
SITES = {
    "leboncoin.fr": ("leboncoin", r"/ad/|/\d{6,}\b|/annonces?/"),
    "seloger.com": ("seloger", r"/annonces/|/\d{6,}\.htm"),
    "pap.fr": ("pap", r"/annonces/[^/]+-r\d+"),
    "bienici.com": ("bienici", r"/annonce/"),
    "logic-immo.com": ("logic-immo", r"/detail-"),
}


def _decoder_entete(valeur) -> str:
    if not valeur:
        return ""
    morceaux = email.header.decode_header(valeur)
    return "".join(m.decode(enc or "utf-8", "replace") if isinstance(m, bytes) else m for m, enc in morceaux)


def _corps_html(message) -> str:
    html, texte = "", ""
    for part in message.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain"):
            continue
        try:
            contenu = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if ctype == "text/html":
            html += contenu
        else:
            texte += contenu
    return html or ("<pre>" + texte + "</pre>")


def _site(url: str):
    hote = urlparse(url).netloc.lower()
    for domaine, (source, motif) in SITES.items():
        if hote.endswith(domaine):
            return source, motif
    return None, None


def _nettoyer_url(url: str) -> str:
    """Retire les paramètres de suivi (utm…) pour que la même annonce ait toujours la même clé."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


def _bloc(lien):
    """Élément englobant le lien qui contient un prix ou une surface (le « bloc » de l'annonce)."""
    el = lien
    for _ in range(6):
        if el.parent is None:
            break
        el = el.parent
        texte = el.get_text(" ", strip=True)
        if ("€" in texte or "m²" in texte or "m2" in texte) and len(texte) < 1200:
            return el
    return lien.parent or lien


def _ville_dans(texte: str) -> tuple[str, str | None]:
    """Cherche une de vos villes (ou « Ville (94150) », « Ville 94150 ») dans le texte."""
    for ville in config.VILLES:
        if re.search(r"\b" + re.escape(ville) + r"\b", texte, re.I):
            m = re.search(re.escape(ville) + r"\s*\(?(\d{5})\)?", texte, re.I)
            return ville, (m.group(1) if m else None)
    m = re.search(r"([A-ZÉÈÀ][\w'’\- ]{2,40}?)\s*\(?(\d{5})\)?", texte)
    if m:
        return base.nettoyer_ville(m.group(1)), m.group(2)
    return "", None


def extraire_annonces(html: str, villes: list[str] | None = None) -> list[dict]:
    """Annonces trouvées dans le HTML d'un e-mail d'alerte (générique, tous sites reconnus)."""
    soup = BeautifulSoup(html, "html.parser")
    resultat: dict[str, dict] = {}
    for lien in soup.find_all("a", href=True):
        url = lien["href"].strip()
        source, motif = _site(url)
        if not source or not re.search(motif, urlparse(url).path):
            continue
        url = _nettoyer_url(url)
        bloc = _bloc(lien)
        texte = bloc.get_text(" ", strip=True)
        titre = lien.get_text(" ", strip=True)
        titre_du_lien = bool(titre) and "€" not in titre and len(titre) >= 4
        if not titre_du_lien:
            # lien sur une image ou un prix : prendre le début du bloc, avant le loyer
            titre = re.split(r"\d[\d\s\u202f\xa0]*€", texte)[0].strip()[:120] if texte else url
        ville, cp = _ville_dans(texte)
        photos = [img.get("src") for img in bloc.find_all("img") if img.get("src", "").startswith("http")]
        photos = [p for p in photos if not re.search(r"logo|pixel|track|icon|spacer", p, re.I)]
        annonce = base.normaliser_annonce(source, titre, ville, base.extraire_prix(texte),
                                          base.extraire_surface(texte), base.extraire_pieces(texte) or base.extraire_pieces(titre),
                                          url, photos[:1], code_postal=cp)
        annonce["via"] = "alerte"
        annonce["_titre_du_lien"] = titre_du_lien
        ancienne = resultat.get(url)
        # Plusieurs liens vers la même annonce (photo, titre, prix) : garder celui dont le titre est
        # le texte du lien, sinon le plus complet.
        if ancienne is None or (titre_du_lien and not ancienne["_titre_du_lien"]) or (
                titre_du_lien == ancienne["_titre_du_lien"]
                and sum(v is not None for v in annonce.values()) > sum(v is not None for v in ancienne.values())):
            if ancienne is not None and not annonce["photos"]:
                annonce["photos"] = ancienne["photos"]
            resultat[url] = annonce
    for a in resultat.values():
        a.pop("_titre_du_lien", None)
    return list(resultat.values())


def lire_boite(hote: str, utilisateur: str, mot_de_passe: str, dossier: str, jours: int,
               expediteurs: list[str]) -> list[str]:
    """Corps HTML des e-mails d'alerte récents. Lève imaplib.IMAP4.error en cas d'échec de connexion."""
    depuis = (datetime.now() - timedelta(days=jours)).strftime("%d-%b-%Y")
    corps: list[str] = []
    with imaplib.IMAP4_SSL(hote) as boite:
        boite.login(utilisateur, mot_de_passe)
        boite.select(dossier, readonly=True)
        identifiants: set[bytes] = set()
        for exp in expediteurs:
            statut, donnees = boite.search(None, "SINCE", depuis, "FROM", f'"{exp}"')
            if statut == "OK" and donnees and donnees[0]:
                identifiants.update(donnees[0].split())
        log.info("Alertes e-mail : %d message(s) depuis %d jour(s)", len(identifiants), jours)
        for ident in sorted(identifiants, key=lambda x: int(x)):
            statut, donnees = boite.fetch(ident, "(BODY.PEEK[])")
            if statut != "OK" or not donnees or not isinstance(donnees[0], tuple):
                continue
            message = email.message_from_bytes(donnees[0][1])
            corps.append(_corps_html(message))
    return corps


def scraper(criteres: dict) -> list[dict]:
    if not config.ALERTES_IMAP_UTILISATEUR or not config.ALERTES_IMAP_MOT_DE_PASSE:
        log.warning("Alertes e-mail : adresse ou mot de passe d'application non renseigné (Paramètres > Alertes e-mail).")
        return []
    try:
        messages = lire_boite(config.ALERTES_IMAP_HOTE, config.ALERTES_IMAP_UTILISATEUR,
                              config.ALERTES_IMAP_MOT_DE_PASSE, config.ALERTES_DOSSIER,
                              config.ALERTES_JOURS, config.ALERTES_EXPEDITEURS)
    except (imaplib.IMAP4.error, OSError) as exc:
        log.error("Alertes e-mail : connexion IMAP impossible (%s). Vérifiez le serveur, l'adresse et le "
                  "mot de passe d'application.", exc)
        return []
    annonces: dict[str, dict] = {}
    for html in messages:
        for a in extraire_annonces(html, criteres.get("villes")):
            annonces.setdefault(a["url"], a)
    log.info("Alertes e-mail : %d annonce(s) distincte(s) extraite(s)", len(annonces))
    return list(annonces.values())


def completer(session, annonce: dict) -> None:
    """Rien à faire : on ne contacte pas les sites (une seule photo, celle de l'e-mail)."""
