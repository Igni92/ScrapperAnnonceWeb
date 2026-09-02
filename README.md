# ScrapperAnnonceWeb

Scraping d'annonces de location (PAP, Leboncoin, SeLoger), filtrage, notation pondérée,
**analyse automatique des photos** (état du logement, moisissure) par un modèle Claude avec
vision, et **interface web locale** pour consulter les annonces, régler les paramètres et
lancer les traitements.

**Sans facturation à l'usage** : par défaut, l'analyse photo passe par le CLI `claude` en mode
non interactif, donc par votre abonnement Claude Code. Aucune clé API n'est nécessaire.

## Lancer l'application

Prérequis : Claude Code installé et connecté à votre compte (la commande `claude` doit
fonctionner dans un terminal ; sinon lancez `claude` une fois et connectez-vous).

### Windows

1. Installez Python depuis https://www.python.org/downloads/windows/ (bouton "Download Python 3.x").
   Dans l'installeur, **cochez "Add python.exe to PATH"** avant de cliquer sur Install.
   Fermez puis rouvrez l'invite de commandes après l'installation.
2. Dans le dossier du projet, double-cliquez sur **`interface.bat`** : l'environnement est créé
   au premier lancement, puis le navigateur s'ouvre sur http://127.0.0.1:5000.

`lancer.bat` reste disponible pour la version console (`lancer.bat --skip-photos`, etc.).

### Linux / macOS

```bash
cd ScrapperAnnonceWeb
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py            # interface web (ouvre le navigateur)
python main.py           # ou version console : scraping + analyse + classement
```

## Interface web

- **Annonces** : liste triable et filtrable (site, ville, moisissure, score minimum, favoris,
  masquées, recherche texte), statistiques, vignette, score global et score d'état, badge
  rouge `⚠ moisissure`. Actions rapides : favori, masquer, ouvrir l'annonce.
- **Fiche annonce** : galerie photos, résumé et points négatifs de l'analyse, détail des
  sous-scores (trajet, prix, surface, état) avec leur poids, notes personnelles,
  ré-analyse des photos, suppression.
- **Lancer** : scraping complet, scraping rapide (sans photos), analyse des photos en
  attente, recalcul des scores. Journal en direct, un seul traitement à la fois.
- **Paramètres** : villes, sites, loyer / surface / pièces, destinations et temps de trajet
  maximum, pondération (avec contrôle de la somme), pénalité moisissure, moteur et réglages
  de l'analyse photo. Sauvegardés dans `settings.json`.

## Version console

```bash
python main.py                          # scraping + analyse photo des NOUVELLES annonces + classement
python main.py --skip-photos            # passage rapide (prix / trajet / surface), aucune analyse photo
python main.py --analyser-manquantes    # rattrape les annonces en base jamais analysées
python main.py --recalculer             # recalcule tous les scores avec la pondération actuelle
python main.py --no-scrape              # affiche seulement le classement de la base
python -m pytest -q tests               # tests (aucun appel réseau ni modèle)
```

## Structure

| Fichier | Rôle |
|---|---|
| `app.py`, `templates/` | interface web (Flask) |
| `jobs.py` | exécution en arrière-plan des traitements + capture du journal |
| `config.py` | défauts + liste des paramètres modifiables (persistés dans `settings.json`) |
| `scrapers/` | un module par site, chacun expose `scraper(criteres) -> list[dict]` avec le champ `photos` |
| `trajets.py` | géocodage et temps de trajet réels (transports / voiture) avec cache |
| `communes.py` | contours des communes, trajets par commune, villes suggérées |
| `photo_analysis.py` | analyse des photos (backend `claude_code` ou `api`), JSON strict, lots avec throttling |
| `db.py` | SQLite (table `annonces`), cache des analyses photo clé sur l'URL, favoris, notes |
| `scoring.py` | `calculer_score(prix, surface, ville, score_etat, moisissure_detectee)` -> 0-100 |
| `main.py` | pipeline `executer(mode)` partagé par la console et le web |

## Analyse photo

- Chaque annonce ne conserve que `MAX_PHOTOS_PAR_ANNONCE` photos (6 par défaut).
- Backend `claude_code` (défaut) : les photos sont téléchargées dans un dossier temporaire et
  `claude -p` les lit avec l'outil Read, avec un schéma JSON imposé (`--json-schema`) :
  `{"score_etat": 0-100, "moisissure_detectee": bool, "points_negatifs": [...], "resume": "..."}`.
  Modèle réglable (`sonnet` ou `opus`). Chaque annonce consomme du quota de l'abonnement ;
  le throttling évite d'épuiser la fenêtre d'utilisation d'un coup.
- Backend `api` (`ANTHROPIC_API_KEY`) : SDK Anthropic, même schéma, facturé à l'usage.
- Moins de `PHOTO_MIN_POUR_ANALYSE` photos exploitables => score neutre, aucune pénalité.
- **Cache** : une URL déjà en base n'est jamais ré-analysée. Une erreur technique n'est pas
  mise en cache : l'annonce reste "à analyser" et sera reprise par « Analyser les photos en attente ».

## Trajets réels

Dans **Paramètres → Trajets**, ajoutez une ou plusieurs destinations (nom, adresse ou ville,
mode transports en commun ou voiture, durée maximale). Pour chaque nouvelle annonce, le trajet
est calculé depuis son adresse (ou sa ville et son code postal) vers chaque destination, au
prochain jour de semaine à l'heure de référence (08:30 par défaut). Une annonce qui dépasse le
maximum d'une destination est écartée avant l'analyse photo. La durée la plus longue sert au
sous-score trajet.

Services utilisés, gratuits et sans clé, à réserver à un usage personnel léger :

| Service | Rôle | Politique |
|---|---|---|
| Base Adresse Nationale (data.geopf.fr) | géocodage des villes / adresses | 50 requêtes/s |
| OSRM, serveur de démonstration FOSSGIS | trajets en voiture | 1 requête/s, non commercial |
| Transitous (api.transitous.org) | trajets en transports en commun | projet bénévole, non commercial, User-Agent avec contact |

Les résultats sont mis en cache en base (tables `geocodage` et `trajets_cache`) : les annonces
d'une même ville partagent le même calcul, donc quelques requêtes par lancement seulement.
Après modification des destinations, lancez « Recalculer » pour mettre à jour les annonces
existantes.

## Carte interactive et communes suggérées

Dans **Paramètres → Recherche**, une carte affiche les communes des départements choisis
(`CARTE_DEPARTEMENTS`, 75/92/93/94 par défaut). Cliquez sur une commune pour l'ajouter ou la
retirer des villes recherchées : le champ texte et la carte restent synchronisés.

Lancez **« Évaluer les communes »** (page Lancer) pour calculer le trajet de chaque commune
vers vos destinations (quelques minutes, résultats conservés en base). La carte se colore
alors : vert = dans les temps de trajet, rouge = trop loin, gris = non évaluée. Les communes
vertes absentes de votre liste sont proposées sous la carte, à ajouter d'un clic
(individuellement ou « Tout ajouter »). L'option « Inclure automatiquement les communes
suggérées » les ajoute au scraping sans passer par la liste.

Contours fournis par geo.api.gouv.fr (avec secours france-geojson et opendata.paris.fr),
fond de carte OpenStreetMap, bibliothèque Leaflet embarquée dans `static/leaflet/`.
Les contours sont mis en cache dans le dossier `cache/`.

## Notation

```
score = POIDS_TRAJET * trajet + POIDS_PRIX * prix + POIDS_SURFACE * surface + POIDS_PHOTO * score_etat
si moisissure_detectee : score = min(score - MALUS_MOISISSURE, PLAFOND_SCORE_MOISISSURE)
```
