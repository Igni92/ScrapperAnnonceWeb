# ScrapperAnnonceWeb

Scraping d'annonces de location (PAP, Leboncoin, SeLoger), filtrage, notation pondérée
et **analyse automatique des photos** (état du logement, moisissure) par un modèle Claude
avec vision.

**Sans facturation à l'usage** : par défaut, l'analyse photo passe par le CLI `claude` en mode
non interactif, donc par votre abonnement Claude Code. Aucune clé API n'est nécessaire.

## Structure

| Fichier | Rôle |
|---|---|
| `config.py` | critères de filtrage, pondération (`POIDS_TRAJET/PRIX/SURFACE/PHOTO`), réglages de l'analyse photo |
| `scrapers/` | un module par site, chacun expose `scraper(criteres) -> list[dict]` avec le champ `photos` |
| `photo_analysis.py` | analyse des photos (backend `claude_code` ou `api`), JSON strict, lots avec throttling |
| `db.py` | SQLite (table `annonces`), cache des analyses photo clé sur l'URL |
| `scoring.py` | `calculer_score(prix, surface, ville, score_etat, moisissure_detectee)` -> 0-100 |
| `main.py` | orchestration + affichage du classement |

## Lancer l'application

Prérequis : Python 3.10+, et Claude Code installé et connecté à votre compte (`claude` doit
fonctionner dans un terminal ; si ce n'est pas le cas, lancez `claude` une fois et connectez-vous).

```bash
cd ScrapperAnnonceWeb
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt

python main.py                     # scraping + analyse photo des NOUVELLES annonces + classement
```

Autres commandes utiles :

```bash
python main.py --skip-photos            # passage rapide (prix / trajet / surface), aucune analyse photo
python main.py --analyser-manquantes    # rattrape les annonces en base jamais analysées
python main.py --no-scrape              # affiche seulement le classement de la base
python main.py --sources pap,leboncoin --max-photos 4 --limite 50 -v
python -m pytest -q tests               # tests (aucun appel réseau ni modèle)
```

Adaptez d'abord `config.py` : villes, loyer max, surface min, temps de trajet par ville.

## Analyse photo

- Chaque annonce ne conserve que `MAX_PHOTOS_PAR_ANNONCE` photos (6 par défaut).
- Backend `claude_code` (défaut) : les photos sont téléchargées dans un dossier temporaire et
  `claude -p` les lit avec l'outil Read, avec un schéma JSON imposé (`--json-schema`) :
  `{"score_etat": 0-100, "moisissure_detectee": bool, "points_negatifs": [...], "resume": "..."}`.
  Modèle réglable via `PHOTO_CLAUDE_MODEL` (`sonnet` ou `opus`). Chaque annonce consomme du
  quota de l'abonnement ; le throttling évite d'épuiser la fenêtre d'utilisation d'un coup.
- Backend `api` (`PHOTO_BACKEND = "api"` + `ANTHROPIC_API_KEY`) : SDK Anthropic, même schéma,
  facturé à l'usage indépendamment de l'abonnement.
- Moins de `PHOTO_MIN_POUR_ANALYSE` photos exploitables => score neutre (`PHOTO_SCORE_NEUTRE`),
  aucune pénalité.
- **Cache** : le résultat est stocké dans `annonces` (`score_etat`, `moisissure_detectee`,
  `points_negatifs`, `resume_etat`, `photo_analyse_le`). Une URL déjà en base n'est jamais
  ré-analysée. Une erreur technique (CLI absent, limite atteinte, JSON invalide) n'est pas mise
  en cache : l'annonce reste "à analyser" et sera reprise par `--analyser-manquantes`.
- **Throttling** : lots de `PHOTO_TAILLE_LOT` annonces, pause `PHOTO_DELAI_ENTRE_APPELS` entre
  appels et `PHOTO_PAUSE_ENTRE_LOTS` entre lots, `PHOTO_MAX_PAR_RUN` annonces max par exécution,
  reprise avec attente croissante si la limite d'utilisation est atteinte.

## Notation

```
score = 0.34 * trajet + 0.30 * prix + 0.21 * surface + 0.15 * score_etat
si moisissure_detectee : score = min(score - MALUS_MOISISSURE, PLAFOND_SCORE_MOISISSURE)
```

Les annonces avec moisissure sont signalées `⚠ MOISISSURE` dans le classement et listées
à part en fin d'affichage.
