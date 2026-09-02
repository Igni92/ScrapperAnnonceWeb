# ScrapperAnnonceWeb

Scraping d'annonces de location (PAP, Leboncoin, SeLoger), filtrage, notation pondérée
et **analyse automatique des photos** (état du logement, moisissure) via l'API Anthropic.

## Structure

| Fichier | Rôle |
|---|---|
| `config.py` | critères de filtrage, pondération (`POIDS_TRAJET/PRIX/SURFACE/PHOTO`), réglages de l'analyse photo |
| `scrapers/` | un module par site, chacun expose `scraper(criteres) -> list[dict]` avec le champ `photos` |
| `photo_analysis.py` | envoi des photos au modèle vision, réponse JSON stricte, traitement par lots avec throttling |
| `db.py` | SQLite (table `annonces`), cache des analyses photo clé sur l'URL |
| `scoring.py` | `calculer_score(prix, surface, ville, score_etat, moisissure_detectee)` -> 0-100 |
| `main.py` | orchestration + affichage du classement |

## Installation

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

## Utilisation

```bash
python main.py                          # scraping + analyse photo des NOUVELLES annonces + classement
python main.py --skip-photos            # passage rapide (prix / trajet / surface), sans appel API
python main.py --analyser-manquantes    # rattrape les annonces en base jamais analysées
python main.py --no-scrape              # affiche seulement le classement de la base
python main.py --sources pap,leboncoin --max-photos 4 --limite 50 -v
```

## Analyse photo

- Chaque annonce ne conserve que `MAX_PHOTOS_PAR_ANNONCE` photos (6 par défaut).
- Les photos sont téléchargées, encodées en base64 et envoyées en une seule requête au modèle
  `PHOTO_MODEL` (`claude-sonnet-4-6` par défaut) avec un schéma JSON imposé :
  `{"score_etat": 0-100, "moisissure_detectee": bool, "points_negatifs": [...], "resume": "..."}`.
- Moins de `PHOTO_MIN_POUR_ANALYSE` photos exploitables => score neutre (`PHOTO_SCORE_NEUTRE`),
  aucune pénalité.
- **Cache** : le résultat est stocké dans `annonces` (`score_etat`, `moisissure_detectee`,
  `points_negatifs`, `resume_etat`, `photo_analyse_le`). Une URL déjà en base n'est jamais
  ré-analysée. Une erreur technique (API injoignable, JSON invalide) n'est pas mise en cache :
  l'annonce reste "à analyser" et sera reprise par `--analyser-manquantes`.
- **Throttling** : lots de `PHOTO_TAILLE_LOT` annonces, pause `PHOTO_DELAI_ENTRE_APPELS` entre
  appels et `PHOTO_PAUSE_ENTRE_LOTS` entre lots, `PHOTO_MAX_PAR_RUN` annonces max par exécution,
  reprise avec attente croissante sur limite de débit.

## Notation

```
score = 0.34 * trajet + 0.30 * prix + 0.21 * surface + 0.15 * score_etat
si moisissure_detectee : score = min(score - MALUS_MOISISSURE, PLAFOND_SCORE_MOISISSURE)
```

Les annonces avec moisissure sont signalées `⚠ MOISISSURE` dans le classement et listées
à part en fin d'affichage.

## Tests

```bash
python -m pytest -q tests
```

Les tests n'appellent ni les sites ni l'API (modèle et téléchargements simulés).
