"""Configuration du scraper : critères de filtrage et pondération de la notation.

Les valeurs ci-dessous sont les défauts. Celles listées dans PARAMETRES sont modifiables
depuis l'interface web et persistées dans settings.json (chargé automatiquement à l'import).
"""

import json
from pathlib import Path

SETTINGS_PATH = Path(__file__).with_name("settings.json")

# ---------------------------------------------------------------------------
# Critères de recherche / filtrage
# ---------------------------------------------------------------------------
VILLES = ["Paris", "Montreuil", "Vincennes", "Saint-Mandé"]
SOURCES_ACTIVES = ["pap", "leboncoin", "seloger"]
PRIX_MAX = 1500          # loyer mensuel max (€)
PRIX_MIN_REF = 700       # loyer en dessous duquel le sous-score prix est à 100
SURFACE_MIN = 30         # m²
SURFACE_MAX_REF = 70     # surface à partir de laquelle le sous-score surface est à 100
PIECES_MIN = 2

# Temps de trajet estimé (minutes) par ville vers le lieu de travail.
# Une ville absente de la table prend TRAJET_DEFAUT.
TRAJET_MINUTES = {
    "Paris": 20,
    "Montreuil": 30,
    "Vincennes": 25,
    "Saint-Mandé": 25,
}
TRAJET_DEFAUT = 45
TRAJET_MAX_REF = 60      # trajet à partir duquel le sous-score trajet est à 0

# ---------------------------------------------------------------------------
# Pondération de la notation (somme = 1.0)
# Avant l'ajout des photos : TRAJET 0.40 / PRIX 0.35 / SURFACE 0.25.
# Rééquilibré proportionnellement (x0.85) pour laisser 0.15 à l'état du logement.
# ---------------------------------------------------------------------------
POIDS_TRAJET = 0.34
POIDS_PRIX = 0.30
POIDS_SURFACE = 0.21
POIDS_PHOTO = 0.15

# ---------------------------------------------------------------------------
# Analyse photo (vision)
# ---------------------------------------------------------------------------
# Backend d'analyse :
#   "claude_code" : passe par le CLI `claude` en mode non interactif (claude -p). Couvert par
#                   l'abonnement Claude Code, aucune clé API ni facturation à l'usage.
#   "api"         : SDK Anthropic (ANTHROPIC_API_KEY), facturé à l'usage, indépendant de l'abonnement.
PHOTO_BACKEND = "claude_code"

# Backend claude_code : exécutable et alias de modèle passés à `claude -p --model`.
PHOTO_CLAUDE_CLI = "claude"
PHOTO_CLAUDE_MODEL = "sonnet"          # "sonnet" (rapide) ou "opus" (plus précis, consomme plus de quota)
PHOTO_CLAUDE_TIMEOUT = 240             # secondes max par annonce

# Backend api : modèle avec vision. Alternatives : "claude-sonnet-5", "claude-opus-5".
PHOTO_MODEL = "claude-sonnet-4-6"
MAX_PHOTOS_PAR_ANNONCE = 6       # 5-6 photos max pour limiter coût et temps
PHOTO_MIN_POUR_ANALYSE = 2       # en dessous, on ne juge pas : score neutre
PHOTO_SCORE_NEUTRE = 60          # score_etat attribué quand on ne peut pas juger
PHOTO_TAILLE_MAX_OCTETS = 4_500_000   # l'API refuse les images > 5 Mo
PHOTO_TIMEOUT_TELECHARGEMENT = 15     # secondes

# Throttling : pause entre deux appels et taille des lots.
PHOTO_DELAI_ENTRE_APPELS = 1.0   # secondes
PHOTO_TAILLE_LOT = 10            # nombre d'annonces par lot
PHOTO_PAUSE_ENTRE_LOTS = 5.0     # secondes entre deux lots
PHOTO_MAX_PAR_RUN = 100          # garde-fou : nombre max d'annonces analysées par exécution

# Pénalité moisissure : malus fixe retiré du score final ET plafond du score final.
MALUS_MOISISSURE = 20
PLAFOND_SCORE_MOISISSURE = 40

# ---------------------------------------------------------------------------
# Base de données / scraping / interface web
# ---------------------------------------------------------------------------
DB_PATH = "annonces.db"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
SCRAPER_TIMEOUT = 20
SCRAPER_DELAI = 1.5      # pause entre deux requêtes vers un même site

WEB_HOTE = "127.0.0.1"
WEB_PORT = 5000

# ---------------------------------------------------------------------------
# Paramètres modifiables depuis l'interface web
# (nom, type, section, libellé, aide). Types : int, float, str, liste, dict_int, choix:a|b
# ---------------------------------------------------------------------------
PARAMETRES = [
    ("VILLES", "liste", "Recherche", "Villes recherchées", "Séparées par des virgules."),
    ("SOURCES_ACTIVES", "sources", "Recherche", "Sites à scraper", ""),
    ("PRIX_MAX", "int", "Recherche", "Loyer maximum (€)", "Les annonces plus chères sont écartées."),
    ("SURFACE_MIN", "int", "Recherche", "Surface minimale (m²)", "Les annonces plus petites sont écartées."),
    ("PIECES_MIN", "int", "Recherche", "Nombre de pièces minimum", ""),
    ("PRIX_MIN_REF", "int", "Notation", "Loyer « idéal » (€)", "En dessous, le sous-score prix vaut 100."),
    ("SURFACE_MAX_REF", "int", "Notation", "Surface « idéale » (m²)", "Au-dessus, le sous-score surface vaut 100."),
    ("TRAJET_MINUTES", "dict_int", "Notation", "Temps de trajet par ville (min)",
     "Une ligne par ville : « Paris: 20 »."),
    ("TRAJET_DEFAUT", "int", "Notation", "Trajet par défaut (min)", "Pour une ville absente de la table."),
    ("TRAJET_MAX_REF", "int", "Notation", "Trajet maximal (min)", "À partir de cette durée, le sous-score trajet vaut 0."),
    ("POIDS_TRAJET", "float", "Pondération", "Poids du trajet", ""),
    ("POIDS_PRIX", "float", "Pondération", "Poids du prix", ""),
    ("POIDS_SURFACE", "float", "Pondération", "Poids de la surface", ""),
    ("POIDS_PHOTO", "float", "Pondération", "Poids de l'état (photos)", "La somme des quatre poids doit faire 1."),
    ("MALUS_MOISISSURE", "int", "Pondération", "Malus moisissure (points)", ""),
    ("PLAFOND_SCORE_MOISISSURE", "int", "Pondération", "Score maximum si moisissure", ""),
    ("PHOTO_BACKEND", "choix:claude_code|api", "Analyse photo", "Moteur d'analyse",
     "claude_code = abonnement Claude Code (aucune clé API). api = clé ANTHROPIC_API_KEY, facturé à l'usage."),
    ("PHOTO_CLAUDE_MODEL", "choix:sonnet|opus", "Analyse photo", "Modèle (claude_code)", ""),
    ("PHOTO_MODEL", "str", "Analyse photo", "Modèle (api)", ""),
    ("MAX_PHOTOS_PAR_ANNONCE", "int", "Analyse photo", "Photos max par annonce", ""),
    ("PHOTO_MIN_POUR_ANALYSE", "int", "Analyse photo", "Photos min pour juger", "En dessous : score neutre."),
    ("PHOTO_SCORE_NEUTRE", "int", "Analyse photo", "Score neutre", "Attribué quand on ne peut pas juger."),
    ("PHOTO_DELAI_ENTRE_APPELS", "float", "Analyse photo", "Pause entre deux annonces (s)", ""),
    ("PHOTO_TAILLE_LOT", "int", "Analyse photo", "Taille des lots", ""),
    ("PHOTO_PAUSE_ENTRE_LOTS", "float", "Analyse photo", "Pause entre deux lots (s)", ""),
    ("PHOTO_MAX_PAR_RUN", "int", "Analyse photo", "Annonces analysées max par lancement", ""),
]
NOMS_PARAMETRES = [p[0] for p in PARAMETRES]


def exporter() -> dict:
    """Valeurs courantes des paramètres modifiables."""
    return {nom: globals()[nom] for nom in NOMS_PARAMETRES}


def appliquer(valeurs: dict) -> None:
    """Applique des valeurs (déjà typées) aux paramètres modifiables, sans les persister."""
    for nom, valeur in valeurs.items():
        if nom in NOMS_PARAMETRES:
            globals()[nom] = valeur


def enregistrer(valeurs: dict, chemin: Path | None = None) -> None:
    """Applique puis persiste les paramètres dans settings.json."""
    appliquer(valeurs)
    (chemin or SETTINGS_PATH).write_text(
        json.dumps(exporter(), ensure_ascii=False, indent=2), encoding="utf-8")


def charger(chemin: Path | None = None) -> None:
    """Recharge settings.json s'il existe (appelé à l'import)."""
    chemin = chemin or SETTINGS_PATH
    if chemin.exists():
        try:
            appliquer(json.loads(chemin.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass


charger()
