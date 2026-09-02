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
VILLES = ["Rungis", "Chevilly-Larue", "L'Haÿ-les-Roses", "Thiais", "Fresnes", "Villejuif", "Orly"]
# Écarter les annonces dont la ville n'est pas dans VILLES (ni dans les communes suggérées) :
# les sites renvoient souvent des annonces des villes voisines.
FILTRER_VILLES = True
# Sources : "pap" (scraping direct), "alertes" (e-mails d'alerte Leboncoin / SeLoger, recommandé),
# "leboncoin" et "seloger" en accès direct sont bloqués par ces sites (voir SOURCES_NAVIGATEUR).
SOURCES_ACTIVES = ["pap", "alertes"]
# Carte interactive : départements dont les communes sont affichées et évaluées.
CARTE_DEPARTEMENTS = ["75", "92", "93", "94"]
# Ajouter automatiquement au scraping les communes qui respectent les temps de trajet
# (calculées par « Évaluer les communes » sur la page Lancer), même hors de VILLES.
SUGGESTIONS_AUTO = False
PRIX_MAX = 1500          # loyer mensuel max (€)
PRIX_MIN_REF = 700       # loyer en dessous duquel le sous-score prix est à 100
SURFACE_MIN = 30         # m²
SURFACE_MAX_REF = 70     # surface à partir de laquelle le sous-score surface est à 100
PIECES_MIN = 2
# Garde-fous contre les erreurs de lecture des sites : en dessous, la valeur est jugée
# implausible et remplacée par « inconnu » (l'annonce n'est pas écartée, elle est signalée).
PRIX_MIN_PLAUSIBLE = 200     # €/mois
SURFACE_MIN_PLAUSIBLE = 9    # m²

# ---------------------------------------------------------------------------
# Destinations et temps de trajet réels
# ---------------------------------------------------------------------------
# Chaque destination : nom, adresse (géocodée automatiquement), mode ("transport" ou "voiture")
# et durée maximale en minutes (None = pas de filtre, sert seulement à la notation).
# Le trajet est calculé depuis l'adresse (ou à défaut la ville + code postal) de chaque annonce.
# Une annonce dépassant le maximum d'une destination est écartée.
DESTINATIONS = [
    {"nom": "Rungis", "adresse": "Rungis", "mode": "transport", "max_minutes": 30},
]
TRAJET_HEURE_DEPART = "08:30"    # heure de départ de référence (jour de semaine, heure de Paris)
TRAJET_INCONNU_EXCLURE = False   # écarter les annonces dont le trajet n'a pas pu être calculé ?
TRAJET_MAX_REF = 60              # trajet (min) à partir duquel le sous-score trajet est à 0

# Services gratuits utilisés (aucune clé nécessaire), usage personnel et léger uniquement :
#   géocodage : Base Adresse Nationale sur la Géoplateforme (data.geopf.fr)
#   voiture   : OSRM, serveur de démonstration FOSSGIS (1 requête/s max, non commercial)
#   transport : Transitous (api.transitous.org), projet bénévole : usage non commercial,
#               léger, et User-Agent avec un contact (voir TRAJET_USER_AGENT)
GEOCODAGE_URL = "https://data.geopf.fr/geocodage/search"
OSRM_URL = "https://router.project-osrm.org"
TRANSITOUS_URL = "https://api.transitous.org"
TRAJET_USER_AGENT = "ScrapperAnnonceWeb/1.0 (+https://github.com/Igni92/ScrapperAnnonceWeb)"
TRAJET_TIMEOUT = 30              # secondes par appel
TRAJET_DELAI = 1.0               # pause entre deux appels réseau (politique OSRM : 1 req/s)

# Secours si aucune destination n'est configurée ou si le calcul échoue :
# temps de trajet estimé (minutes) par ville. Ville absente => TRAJET_DEFAUT.
TRAJET_MINUTES = {
    "Rungis": 5,
    "Chevilly-Larue": 15,
    "L'Haÿ-les-Roses": 20,
    "Villejuif": 25,
}
TRAJET_DEFAUT = 45

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
SCRAPER_DELAI = 1.5          # pause entre deux requêtes vers un même site (pages de résultats)
SCRAPER_DELAI_DETAIL = 3.0   # pause entre deux pages d'annonce (photos), plus sensibles au blocage
SCRAPER_ATTENTE_429 = 20.0   # attente avant nouvel essai après un « 429 Too Many Requests »

# Alertes e-mail (source "alertes") : l'application lit les e-mails d'alerte envoyés par
# Leboncoin / SeLoger dans votre boîte, via IMAP. Avec Gmail : activez IMAP et créez un
# « mot de passe d'application » (compte Google > Sécurité), à saisir ici, pas votre mot de passe.
ALERTES_IMAP_HOTE = "imap.gmail.com"
ALERTES_IMAP_UTILISATEUR = ""
ALERTES_IMAP_MOT_DE_PASSE = ""
ALERTES_DOSSIER = "INBOX"
ALERTES_JOURS = 7                  # ne lire que les e-mails des N derniers jours
ALERTES_EXPEDITEURS = ["leboncoin", "seloger"]

# Navigateur piloté (Playwright) : option déconseillée, désactivée par défaut (voir navigateur.py).
SOURCES_NAVIGATEUR = []
NAVIGATEUR_VISIBLE = True          # fenêtre visible : c'est à vous de passer une vérification
NAVIGATEUR_ATTENTE_CAPTCHA = 90    # secondes d'attente maximale devant une page de vérification

WEB_HOTE = "127.0.0.1"
WEB_PORT = 5000

# ---------------------------------------------------------------------------
# Paramètres modifiables depuis l'interface web
# (nom, type, section, libellé, aide).
# Types : int, float, str, secret, bool, liste, villes, dict_int, sources, destinations, choix:a|b
# ---------------------------------------------------------------------------
PARAMETRES = [
    ("VILLES", "villes", "Recherche", "Villes recherchées",
     "Séparées par des virgules, ou cliquez sur les communes de la carte."),
    ("CARTE_DEPARTEMENTS", "liste", "Recherche", "Départements affichés sur la carte",
     "Codes séparés par des virgules (75, 92, 93, 94, 91, 77, 78, 95)."),
    ("SUGGESTIONS_AUTO", "bool", "Recherche", "Inclure automatiquement les communes suggérées",
     "Au scraping, ajoute les communes de la carte qui respectent les temps de trajet."),
    ("FILTRER_VILLES", "bool", "Recherche", "Écarter les annonces hors des villes recherchées",
     "Les sites renvoient souvent des annonces des villes voisines (ex. Paris pour Vincennes)."),
    ("SOURCES_ACTIVES", "sources", "Recherche", "Sources",
     "pap = site PAP ; alertes = e-mails d'alerte Leboncoin / SeLoger (recommandé pour ces deux sites)."),
    ("PRIX_MAX", "int", "Recherche", "Loyer maximum (€)", "Les annonces plus chères sont écartées."),
    ("SURFACE_MIN", "int", "Recherche", "Surface minimale (m²)", "Les annonces plus petites sont écartées."),
    ("PIECES_MIN", "int", "Recherche", "Nombre de pièces minimum", ""),
    ("PRIX_MIN_REF", "int", "Notation", "Loyer « idéal » (€)", "En dessous, le sous-score prix vaut 100."),
    ("SURFACE_MAX_REF", "int", "Notation", "Surface « idéale » (m²)", "Au-dessus, le sous-score surface vaut 100."),
    ("DESTINATIONS", "destinations", "Trajets", "Destinations",
     "Temps de trajet réel calculé depuis chaque annonce. Une annonce au-delà du maximum est écartée."),
    ("TRAJET_HEURE_DEPART", "str", "Trajets", "Heure de départ de référence", "Format HH:MM, jour de semaine."),
    ("TRAJET_INCONNU_EXCLURE", "bool", "Trajets", "Écarter si trajet incalculable", "Sinon l'annonce est gardée avec un trajet inconnu."),
    ("TRAJET_MAX_REF", "int", "Trajets", "Trajet maximal pour la notation (min)", "À partir de cette durée, le sous-score trajet vaut 0."),
    ("TRAJET_MINUTES", "dict_int", "Trajets", "Secours : trajet estimé par ville (min)",
     "Utilisé seulement sans destination ou si le calcul échoue. Une ligne par ville : « Paris: 20 »."),
    ("TRAJET_DEFAUT", "int", "Trajets", "Secours : trajet par défaut (min)", "Pour une ville absente de la table."),
    ("POIDS_TRAJET", "float", "Pondération", "Poids du trajet", ""),
    ("POIDS_PRIX", "float", "Pondération", "Poids du prix", ""),
    ("POIDS_SURFACE", "float", "Pondération", "Poids de la surface", ""),
    ("POIDS_PHOTO", "float", "Pondération", "Poids de l'état (photos)", "La somme des quatre poids doit faire 1."),
    ("MALUS_MOISISSURE", "int", "Pondération", "Malus moisissure (points)", ""),
    ("PLAFOND_SCORE_MOISISSURE", "int", "Pondération", "Score maximum si moisissure", ""),
    ("ALERTES_IMAP_HOTE", "str", "Alertes e-mail", "Serveur IMAP", "imap.gmail.com pour Gmail, outlook.office365.com pour Outlook."),
    ("ALERTES_IMAP_UTILISATEUR", "str", "Alertes e-mail", "Adresse e-mail", ""),
    ("ALERTES_IMAP_MOT_DE_PASSE", "secret", "Alertes e-mail", "Mot de passe d'application",
     "Gmail : compte Google > Sécurité > Mots de passe des applications. Stocké en clair dans settings.json sur ce PC."),
    ("ALERTES_DOSSIER", "str", "Alertes e-mail", "Dossier", "INBOX par défaut."),
    ("ALERTES_JOURS", "int", "Alertes e-mail", "E-mails des N derniers jours", ""),
    ("ALERTES_EXPEDITEURS", "liste", "Alertes e-mail", "Expéditeurs à lire", "Mots contenus dans l'adresse de l'expéditeur."),
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
