"""Configuration du scraper : critères de filtrage et pondération de la notation."""

# ---------------------------------------------------------------------------
# Critères de recherche / filtrage
# ---------------------------------------------------------------------------
VILLES = ["Paris", "Montreuil", "Vincennes", "Saint-Mandé"]
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
# Modèle avec vision. Alternatives : "claude-sonnet-5" (moins cher), "claude-opus-5" (plus précis).
PHOTO_MODEL = "claude-sonnet-4-6"
MAX_PHOTOS_PAR_ANNONCE = 6       # 5-6 photos max pour limiter coût et temps
PHOTO_MIN_POUR_ANALYSE = 2       # en dessous, on ne juge pas : score neutre
PHOTO_SCORE_NEUTRE = 60          # score_etat attribué quand on ne peut pas juger
PHOTO_TAILLE_MAX_OCTETS = 4_500_000   # l'API refuse les images > 5 Mo
PHOTO_TIMEOUT_TELECHARGEMENT = 15     # secondes

# Throttling : pause entre deux appels API et taille des lots.
PHOTO_DELAI_ENTRE_APPELS = 1.0   # secondes
PHOTO_TAILLE_LOT = 10            # nombre d'annonces par lot
PHOTO_PAUSE_ENTRE_LOTS = 5.0     # secondes entre deux lots
PHOTO_MAX_PAR_RUN = 100          # garde-fou : nombre max d'annonces analysées par exécution

# Pénalité moisissure : malus fixe retiré du score final ET plafond du score final.
MALUS_MOISISSURE = 20
PLAFOND_SCORE_MOISISSURE = 40

# ---------------------------------------------------------------------------
# Base de données
# ---------------------------------------------------------------------------
DB_PATH = "annonces.db"

# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
SCRAPER_TIMEOUT = 20
SCRAPER_DELAI = 1.5      # pause entre deux requêtes vers un même site
