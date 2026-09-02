"""Analyse de l'état d'un logement à partir des photos d'une annonce (vision).

Deux backends, choisis par config.PHOTO_BACKEND :
  - "claude_code" : le CLI `claude` en mode non interactif (`claude -p`), qui lit les photos
                    téléchargées sur disque. Couvert par l'abonnement Claude Code : pas de clé API.
  - "api"         : le SDK Anthropic (facturé à l'usage, clé ANTHROPIC_API_KEY).

Point d'entrée principal : `analyser_annonces(annonces)` qui remplit `annonce["analyse_photo"]`
pour chaque annonce, avec un dict de la forme :

    {
        "score_etat": 0-100,
        "moisissure_detectee": bool,
        "points_negatifs": [...],
        "resume": "...",
        "nb_photos_analysees": int,
        "statut": "ok" | "neutre" | "erreur",
    }

Le cache (ne jamais ré-analyser une URL déjà vue) est géré par db.py / main.py : ce module
ne fait que l'analyse elle-même.
"""

import base64
import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

import requests

import config

log = logging.getLogger(__name__)

MEDIA_TYPES_ACCEPTES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}

SCHEMA_REPONSE = {
    "type": "object",
    "properties": {
        "score_etat": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": "État général du logement : 100 = impeccable, 0 = insalubre.",
        },
        "moisissure_detectee": {
            "type": "boolean",
            "description": "True si moisissure, traces noires d'humidité ou champignons visibles.",
        },
        "points_negatifs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Défauts concrets observés (court, en français). Vide si rien à signaler.",
        },
        "resume": {
            "type": "string",
            "description": "Résumé de 1 à 2 phrases sur l'état du logement, en français.",
        },
    },
    "required": ["score_etat", "moisissure_detectee", "points_negatifs", "resume"],
    "additionalProperties": False,
}

PROMPT_SYSTEME = (
    "Tu es un expert en inspection de logements. On te montre les photos d'une annonce "
    "immobilière de location. Ton rôle est d'évaluer l'ÉTAT du logement, pas son esthétique "
    "ni la décoration : un appartement au mobilier démodé mais propre et sain doit avoir un bon score.\n\n"
    "Évalue :\n"
    "1. Moisissure, traces d'humidité, auréoles, salpêtre, peinture cloquée (signal d'alerte majeur).\n"
    "2. Dégradations visibles : fissures, sols abîmés, joints noircis, menuiseries en mauvais état, "
    "équipements cassés ou très vétustes.\n"
    "3. Propreté générale et état d'entretien.\n"
    "4. Désordre / encombrement qui masquerait des défauts.\n"
    "5. Luminosité réelle (fenêtres, exposition apparente), en distinguant une pièce sombre d'une "
    "photo simplement mal exposée.\n\n"
    "Barème indicatif pour score_etat : 85-100 impeccable / rénové ; 65-84 bon état, usure normale ; "
    "45-64 défauts visibles mais habitable ; 25-44 dégradé, travaux à prévoir ; 0-24 insalubre.\n"
    "Si les photos ne permettent pas de juger (trop peu de photos, photos d'extérieur uniquement, "
    "images floues), reste autour de 60 et dis-le dans le résumé.\n"
    "Ne signale moisissure_detectee=true que si tu vois réellement des traces, pas par précaution.\n"
    "Réponds uniquement avec le JSON demandé, en français."
)


class LimiteDebit(Exception):
    """Limite de débit / quota atteint : le lot doit marquer une pause puis réessayer."""


# ---------------------------------------------------------------------------
# Résultats
# ---------------------------------------------------------------------------
def resultat_neutre(raison: str, nb_photos: int = 0) -> dict:
    """Résultat quand on ne peut pas juger : score neutre, aucune pénalité."""
    return {
        "score_etat": config.PHOTO_SCORE_NEUTRE,
        "moisissure_detectee": False,
        "points_negatifs": [],
        "resume": f"Non évalué : {raison}.",
        "nb_photos_analysees": nb_photos,
        "statut": "neutre",
    }


def _resultat_erreur(raison: str, nb_photos: int = 0) -> dict:
    """Erreur technique : score neutre mais statut 'erreur' pour ne pas le mettre en cache."""
    resultat = resultat_neutre(raison, nb_photos)
    resultat["statut"] = "erreur"
    return resultat


# ---------------------------------------------------------------------------
# Téléchargement des photos
# ---------------------------------------------------------------------------
def telecharger_photo(url: str, session: requests.Session | None = None) -> tuple[str, bytes] | None:
    """Télécharge une image et renvoie (media_type, octets), ou None si inutilisable."""
    session = session or requests.Session()
    try:
        reponse = session.get(
            url,
            timeout=config.PHOTO_TIMEOUT_TELECHARGEMENT,
            headers={"User-Agent": config.USER_AGENT, "Accept": "image/*"},
            stream=True,
        )
        reponse.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Photo inaccessible %s : %s", url, exc)
        return None

    media_type = (reponse.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if media_type == "image/jpg":
        media_type = "image/jpeg"
    if media_type not in MEDIA_TYPES_ACCEPTES:
        log.warning("Type d'image non supporté (%s) pour %s", media_type or "inconnu", url)
        return None

    contenu = bytearray()
    for morceau in reponse.iter_content(chunk_size=64 * 1024):
        contenu.extend(morceau)
        if len(contenu) > config.PHOTO_TAILLE_MAX_OCTETS:
            log.warning("Photo trop lourde (> %d octets), ignorée : %s",
                        config.PHOTO_TAILLE_MAX_OCTETS, url)
            return None
    if not contenu:
        return None
    return media_type, bytes(contenu)


def telecharger_photos(photos: list[str], max_photos: int | None = None,
                       session: requests.Session | None = None) -> list[tuple[str, bytes]]:
    """Télécharge jusqu'à `max_photos` photos exploitables."""
    max_photos = max_photos or config.MAX_PHOTOS_PAR_ANNONCE
    session = session or requests.Session()
    images: list[tuple[str, bytes]] = []
    for url in photos:
        if len(images) >= max_photos:
            break
        image = telecharger_photo(url, session)
        if image is not None:
            images.append(image)
    return images


def _texte_requete(titre: str, nb: int) -> str:
    intro = f"Annonce : « {titre} ». " if titre else ""
    return (f"{intro}Voici {nb} photo(s) de ce logement. Évalue son état selon les consignes "
            "et renvoie uniquement le JSON.")


def _parser_reponse(donnees) -> dict:
    """Normalise le JSON du modèle (dict déjà parsé ou texte)."""
    if isinstance(donnees, str):
        donnees = json.loads(donnees)
    score = int(round(float(donnees["score_etat"])))
    return {
        "score_etat": max(0, min(100, score)),
        "moisissure_detectee": bool(donnees.get("moisissure_detectee", False)),
        "points_negatifs": [str(p) for p in (donnees.get("points_negatifs") or [])],
        "resume": str(donnees.get("resume") or "").strip(),
    }


# ---------------------------------------------------------------------------
# Backend "claude_code" : CLI `claude -p` (abonnement Claude Code)
# ---------------------------------------------------------------------------
def cli_disponible() -> bool:
    return shutil.which(config.PHOTO_CLAUDE_CLI) is not None


def _analyser_via_claude_code(images: list[tuple[str, bytes]], titre: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="photos_annonce_") as dossier:
        chemins = []
        for i, (media_type, octets) in enumerate(images, start=1):
            chemin = Path(dossier) / f"photo_{i}{EXTENSIONS[media_type]}"
            chemin.write_bytes(octets)
            chemins.append(str(chemin))

        liste = "\n".join(f"- {c}" for c in chemins)
        prompt = (f"{_texte_requete(titre, len(chemins))}\n\n"
                  f"Lis chacune de ces images avec l'outil Read avant de répondre :\n{liste}")
        commande = [
            config.PHOTO_CLAUDE_CLI, "-p", prompt,
            "--model", config.PHOTO_CLAUDE_MODEL,
            "--append-system-prompt", PROMPT_SYSTEME,
            "--output-format", "json",
            "--json-schema", json.dumps(SCHEMA_REPONSE, ensure_ascii=False),
            "--tools", "Read",
            "--allowedTools", "Read",
            "--no-session-persistence",
        ]
        try:
            proc = subprocess.run(
                commande, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=config.PHOTO_CLAUDE_TIMEOUT, cwd=dossier,
            )
        except FileNotFoundError:
            log.error("CLI `%s` introuvable : installez Claude Code ou passez PHOTO_BACKEND='api'",
                      config.PHOTO_CLAUDE_CLI)
            return _resultat_erreur("CLI claude introuvable", len(images))
        except subprocess.TimeoutExpired:
            log.error("`claude -p` a dépassé %ds", config.PHOTO_CLAUDE_TIMEOUT)
            return _resultat_erreur("délai dépassé", len(images))

    sortie = (proc.stdout or "").strip()
    if proc.returncode != 0 or not sortie:
        detail = (proc.stderr or sortie or "").strip()[-400:]
        if re.search(r"rate limit|usage limit|limit reached|429", detail, re.I):
            raise LimiteDebit(detail)
        log.error("`claude -p` a échoué (code %s) : %s", proc.returncode, detail)
        return _resultat_erreur("échec du CLI claude", len(images))

    try:
        enveloppe = json.loads(sortie)
    except ValueError:
        log.error("Sortie de `claude -p` non JSON : %r", sortie[:200])
        return _resultat_erreur("sortie CLI non parsable", len(images))

    if enveloppe.get("is_error"):
        detail = str(enveloppe.get("result") or "")[:400]
        if re.search(r"rate limit|usage limit|limit reached|429", detail, re.I):
            raise LimiteDebit(detail)
        log.error("`claude -p` a renvoyé une erreur : %s", detail)
        return _resultat_erreur("erreur renvoyée par le CLI", len(images))

    brut = enveloppe.get("structured_output")
    if brut is None:
        brut = _extraire_json(str(enveloppe.get("result") or ""))
    try:
        resultat = _parser_reponse(brut)
    except (ValueError, KeyError, TypeError) as exc:
        log.error("Réponse JSON invalide (%s) : %r", exc, str(brut)[:200])
        return _resultat_erreur("réponse du modèle non parsable", len(images))
    resultat["nb_photos_analysees"] = len(images)
    resultat["statut"] = "ok"
    return resultat


def _extraire_json(texte: str):
    """Tolère un JSON entouré de texte ou de ``` : renvoie le premier objet {...} trouvé."""
    m = re.search(r"\{.*\}", texte, re.S)
    return m.group(0) if m else texte


# ---------------------------------------------------------------------------
# Backend "api" : SDK Anthropic (facturé à l'usage)
# ---------------------------------------------------------------------------
_client = None


def get_client():
    """Client Anthropic partagé (clé lue depuis ANTHROPIC_API_KEY)."""
    global _client
    if _client is None:
        import anthropic  # import différé : inutile avec le backend claude_code
        _client = anthropic.Anthropic()
    return _client


def _analyser_via_api(images: list[tuple[str, bytes]], titre: str, client=None) -> dict:
    import anthropic

    contenu = [
        {"type": "image",
         "source": {"type": "base64", "media_type": media_type,
                    "data": base64.standard_b64encode(octets).decode("ascii")}}
        for media_type, octets in images
    ]
    contenu.append({"type": "text", "text": _texte_requete(titre, len(images))})

    client = client or get_client()
    try:
        reponse = client.messages.create(
            model=config.PHOTO_MODEL,
            max_tokens=1024,
            system=PROMPT_SYSTEME,
            messages=[{"role": "user", "content": contenu}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA_REPONSE}},
        )
    except anthropic.RateLimitError as exc:
        # Le SDK a déjà retenté avec backoff : on lève pour que le lot marque une pause.
        raise LimiteDebit(str(exc)) from exc
    except anthropic.BadRequestError as exc:
        # Typiquement : image trop grande / dimensions hors limites. Pas de cache, on réessaiera.
        log.error("Requête refusée par l'API (%s) : %s", exc.status_code, exc.message)
        return _resultat_erreur("requête refusée par l'API", len(images))
    except anthropic.APIStatusError as exc:
        log.error("Erreur API %s : %s", exc.status_code, exc.message)
        return _resultat_erreur(f"erreur API {exc.status_code}", len(images))
    except anthropic.APIConnectionError as exc:
        log.error("Connexion à l'API impossible : %s", exc)
        return _resultat_erreur("connexion API impossible", len(images))

    if reponse.stop_reason == "refusal":
        log.warning("Le modèle a refusé d'analyser les photos de « %s »", titre)
        return resultat_neutre("analyse refusée par le modèle", len(images))
    if reponse.stop_reason == "max_tokens":
        return _resultat_erreur("réponse tronquée", len(images))

    texte = next((b.text for b in reponse.content if b.type == "text"), "")
    try:
        resultat = _parser_reponse(texte)
    except (ValueError, KeyError, TypeError) as exc:
        log.error("Réponse JSON invalide (%s) : %r", exc, texte[:200])
        return _resultat_erreur("réponse du modèle non parsable", len(images))
    resultat["nb_photos_analysees"] = len(images)
    resultat["statut"] = "ok"
    return resultat


# ---------------------------------------------------------------------------
# Point d'entrée par annonce
# ---------------------------------------------------------------------------
def analyser_photos(photos: list[str], titre: str = "", client=None,
                    max_photos: int | None = None, session: requests.Session | None = None,
                    backend: str | None = None) -> dict:
    """Analyse les photos d'une annonce et renvoie le dict de résultat (voir en-tête du module)."""
    photos = [p for p in (photos or []) if p]
    if len(photos) < config.PHOTO_MIN_POUR_ANALYSE:
        return resultat_neutre("pas assez de photos dans l'annonce", nb_photos=0)

    images = telecharger_photos(photos, max_photos=max_photos, session=session)
    if len(images) < config.PHOTO_MIN_POUR_ANALYSE:
        return resultat_neutre("photos non téléchargeables", nb_photos=len(images))

    backend = backend or config.PHOTO_BACKEND
    if backend == "api":
        return _analyser_via_api(images, titre, client=client)
    if backend == "claude_code":
        return _analyser_via_claude_code(images, titre)
    raise ValueError(f"PHOTO_BACKEND inconnu : {backend!r} (attendu 'claude_code' ou 'api')")


# ---------------------------------------------------------------------------
# Traitement par lots avec throttling
# ---------------------------------------------------------------------------
def analyser_annonces(annonces: list[dict], client=None, max_photos: int | None = None,
                      apres_chaque: Callable[[dict], None] | None = None,
                      max_par_run: int | None = None) -> list[dict]:
    """Analyse les photos d'une liste d'annonces, par lots, avec pause entre les appels.

    Remplit `annonce["analyse_photo"]` en place. `apres_chaque(annonce)` est appelé après
    chaque annonce (utile pour persister au fil de l'eau). Les annonces au-delà de
    `max_par_run` ne sont pas analysées (elles resteront "à analyser" en base).
    """
    if not annonces:
        return annonces

    max_par_run = max_par_run if max_par_run is not None else config.PHOTO_MAX_PAR_RUN
    a_traiter = annonces[:max_par_run]
    if len(annonces) > len(a_traiter):
        log.warning("%d annonces à analyser, limité à %d pour ce run (PHOTO_MAX_PAR_RUN)",
                    len(annonces), len(a_traiter))

    if config.PHOTO_BACKEND == "api" and client is None:
        client = get_client()
    session = requests.Session()
    total = len(a_traiter)
    taille_lot = max(1, config.PHOTO_TAILLE_LOT)

    for debut in range(0, total, taille_lot):
        lot = a_traiter[debut : debut + taille_lot]
        if debut > 0:
            log.info("Pause de %.0fs entre deux lots", config.PHOTO_PAUSE_ENTRE_LOTS)
            time.sleep(config.PHOTO_PAUSE_ENTRE_LOTS)

        for i, annonce in enumerate(lot, start=debut + 1):
            log.info("[%d/%d] Analyse photos : %s", i, total, annonce.get("titre") or annonce["url"])
            annonce["analyse_photo"] = _analyser_avec_reprise(annonce, client, max_photos, session)
            if apres_chaque:
                apres_chaque(annonce)
            if i < total:
                time.sleep(config.PHOTO_DELAI_ENTRE_APPELS)

    return annonces


def _analyser_avec_reprise(annonce: dict, client, max_photos, session, essais: int = 3) -> dict:
    """Appelle analyser_photos, avec attente croissante en cas de limite de débit."""
    attente = 15.0
    for essai in range(1, essais + 1):
        try:
            return analyser_photos(
                annonce.get("photos") or [],
                titre=annonce.get("titre") or "",
                client=client,
                max_photos=max_photos,
                session=session,
            )
        except LimiteDebit as exc:
            if essai == essais:
                log.error("Limite de débit persistante, annonce laissée non analysée : %s (%s)",
                          annonce["url"], str(exc)[:120])
                return _resultat_erreur("limite de débit")
            log.warning("Limite de débit atteinte, nouvelle tentative dans %.0fs", attente)
            time.sleep(attente)
            attente *= 2
    return _resultat_erreur("limite de débit")
