"""Orchestration : scraping -> filtrage -> analyse photo (nouvelles annonces) -> scoring -> DB -> classement.

`executer(mode, ...)` est le point d'entrée commun à la ligne de commande et à l'interface web.
"""

import argparse
import logging
import sys

import config
import db
import photo_analysis
import scoring
import trajets
from scrapers import SCRAPERS

log = logging.getLogger("main")

MODES = {
    "complet": "Scraping + trajets + analyse photo des nouvelles annonces",
    "rapide": "Scraping + trajets (sans analyse photo)",
    "manquantes": "Analyse photo des annonces en attente",
    "recalculer": "Recalcul des trajets et des scores avec les paramètres actuels",
}


# ---------------------------------------------------------------------------
# Étapes
# ---------------------------------------------------------------------------
def scraper_tout(sources: list[str]) -> list[dict]:
    criteres = {
        "villes": config.VILLES,
        "prix_max": config.PRIX_MAX,
        "surface_min": config.SURFACE_MIN,
        "pieces_min": config.PIECES_MIN,
    }
    annonces: list[dict] = []
    for nom in sources:
        if nom not in SCRAPERS:
            log.warning("Source inconnue ignorée : %s", nom)
            continue
        try:
            resultats = SCRAPERS[nom](criteres)
        except Exception:  # un scraper cassé ne doit pas bloquer les autres
            log.exception("Scraper %s en erreur, ignoré", nom)
            continue
        log.info("%s : %d annonce(s) récupérée(s)", nom, len(resultats))
        annonces.extend(resultats)
    return annonces


def filtrer(annonces: list[dict]) -> list[dict]:
    gardees = []
    for a in annonces:
        if a.get("prix") is not None and a["prix"] > config.PRIX_MAX:
            continue
        if a.get("surface") is not None and a["surface"] < config.SURFACE_MIN:
            continue
        if a.get("pieces") is not None and a["pieces"] < config.PIECES_MIN:
            continue
        gardees.append(a)
    return gardees


def calculer_trajets(conn, annonces: list[dict]) -> list[dict]:
    """Calcule les trajets vers les destinations et écarte les annonces au-delà des maximums."""
    if not config.DESTINATIONS or not annonces:
        return annonces
    log.info("Calcul des trajets vers %s pour %d annonce(s)…",
             ", ".join(d["nom"] for d in config.DESTINATIONS), len(annonces))
    gardees = []
    for a in annonces:
        trajets.calculer_trajets(conn, a)
        if trajets.respecte_maximums(a):
            gardees.append(a)
        else:
            log.info("Écartée (trajet) : %s — %s", a.get("titre") or a["url"], trajets.resume_trajets(a["trajets"]))
    log.info("%d annonce(s) dans les temps de trajet, %d écartée(s)", len(gardees), len(annonces) - len(gardees))
    return gardees


def dedupliquer(annonces: list[dict]) -> list[dict]:
    vues: set[str] = set()
    uniques = []
    for a in annonces:
        if a["url"] in vues:
            continue
        vues.add(a["url"])
        uniques.append(a)
    return uniques


def analyser_nouvelles(conn, nouvelles: list[dict], max_photos: int | None) -> None:
    """Analyse photo des annonces pas encore en base, insertion au fil de l'eau."""
    if not nouvelles:
        return
    log.info("Analyse photo de %d nouvelle(s) annonce(s)…", len(nouvelles))

    def persister(annonce: dict) -> None:
        analyse = annonce.get("analyse_photo") or {}
        if analyse.get("statut") == "erreur":
            # Erreur technique : on insère sans analyse pour pouvoir réessayer plus tard
            # (mode "manquantes"), sans jamais mettre en cache un résultat bidon.
            annonce["analyse_photo"] = None
        annonce["score"] = scoring.calculer_score_annonce(annonce)
        db.inserer_annonce(conn, annonce)

    photo_analysis.analyser_annonces(nouvelles, max_photos=max_photos, apres_chaque=persister)


def analyser_manquantes(conn, max_photos: int | None) -> int:
    """Rattrape les annonces en base sans analyse (insérées en mode rapide ou en erreur)."""
    manquantes = db.annonces_sans_analyse(conn)
    if not manquantes:
        log.info("Aucune annonce en attente d'analyse photo.")
        return 0
    log.info("Rattrapage : %d annonce(s) en base sans analyse photo", len(manquantes))

    def persister(annonce: dict) -> None:
        analyse = annonce.get("analyse_photo") or {}
        if analyse.get("statut") == "erreur":
            return
        score = scoring.calculer_score_annonce(annonce)
        annonce["score"] = score
        db.maj_analyse_photo(conn, annonce["url"], analyse, score=score)

    photo_analysis.analyser_annonces(manquantes, max_photos=max_photos, apres_chaque=persister)
    return len(manquantes)


def recalculer_scores(conn) -> int:
    """Recalcule trajets (avec cache) et score de toutes les annonces (après changement de paramètres)."""
    annonces = db.toutes_annonces(conn)
    if config.DESTINATIONS:
        log.info("Recalcul des trajets vers %s…", ", ".join(d["nom"] for d in config.DESTINATIONS))
    for a in annonces:
        if config.DESTINATIONS:
            trajets.calculer_trajets(conn, a)
        else:
            a["trajets"], a["trajet_minutes"] = None, None
        score = scoring.calculer_score_annonce(a)
        db.maj_trajets(conn, a["url"], a["trajets"], a["trajet_minutes"], score=score)
    log.info("%d score(s) recalculé(s)", len(annonces))
    return len(annonces)


def executer(mode: str, sources: list[str] | None = None, max_photos: int | None = None,
             conn=None) -> dict:
    """Exécute un mode de MODES et renvoie un résumé chiffré."""
    if mode not in MODES:
        raise ValueError(f"Mode inconnu : {mode}")
    conn = conn or db.init_db()
    resume = {"mode": mode, "brutes": 0, "filtrees": 0, "nouvelles": 0, "analysees": 0, "recalculees": 0}
    log.info("Démarrage : %s", MODES[mode])

    if mode in ("complet", "rapide"):
        sources = sources or list(config.SOURCES_ACTIVES)
        brutes = scraper_tout(sources)
        candidates = dedupliquer(filtrer(brutes))
        connues = db.urls_connues(conn, (a["url"] for a in candidates))
        nouvelles = [a for a in candidates if a["url"] not in connues]
        log.info("%d annonce(s) après filtrage (%d brutes), %d nouvelle(s), %d déjà en base",
                 len(candidates), len(brutes), len(nouvelles), len(connues))
        # Trajets uniquement pour les nouvelles annonces (les autres sont déjà en base, avec cache).
        nouvelles = calculer_trajets(conn, nouvelles)
        resume.update(brutes=len(brutes), filtrees=len(candidates), nouvelles=len(nouvelles))
        if mode == "rapide":
            for a in nouvelles:
                a["score"] = scoring.calculer_score_annonce(a)
                db.inserer_annonce(conn, a)
        else:
            analyser_nouvelles(conn, nouvelles, max_photos)
            resume["analysees"] = len(nouvelles)
    elif mode == "manquantes":
        resume["analysees"] = analyser_manquantes(conn, max_photos)
    elif mode == "recalculer":
        resume["recalculees"] = recalculer_scores(conn)

    log.info("Terminé : %s", MODES[mode])
    return resume


# ---------------------------------------------------------------------------
# Affichage console
# ---------------------------------------------------------------------------
def _tronquer(texte: str, longueur: int) -> str:
    texte = texte or ""
    return texte if len(texte) <= longueur else texte[: longueur - 1] + "…"


def afficher_classement(annonces: list[dict], limite: int = 30) -> None:
    classees = sorted(annonces, key=lambda a: (a.get("score") is None, -(a.get("score") or 0)))
    print()
    print(f"{'#':>3}  {'Score':>5}  {'État':>5}  {'Trajet':>6}  {'Prix':>6}  {'Surf.':>6}  {'P.':>2}  "
          f"{'Ville':<14} {'Source':<9} Titre")
    print("-" * 110)
    alertes = []
    for rang, a in enumerate(classees[:limite], start=1):
        analyse = a.get("analyse_photo") or {}
        etat = analyse.get("score_etat")
        etat_txt = f"{etat:>5}" if etat is not None else "  n/a"
        moisissure = bool(analyse.get("moisissure_detectee"))
        drapeau = " ⚠ MOISISSURE" if moisissure else ""
        score = a.get("score")
        trajet = a.get("trajet_minutes")
        print(
            f"{rang:>3}  {score if score is not None else '?':>5}  {etat_txt}  "
            f"{str(int(trajet)) + 'mn' if trajet is not None else 'n/a':>6}  "
            f"{int(a['prix']) if a.get('prix') else '?':>6}  "
            f"{a['surface'] if a.get('surface') else '?':>6}  "
            f"{a['pieces'] if a.get('pieces') else '?':>2}  "
            f"{_tronquer(a.get('ville'), 14):<14} {a['source']:<9} "
            f"{_tronquer(a.get('titre'), 40)}{drapeau}"
        )
        print(f"{'':>3}  {a['url']}")
        if a.get("trajets"):
            print(f"{'':>3}  ↳ {trajets.resume_trajets(a['trajets'])}")
        if analyse.get("resume"):
            print(f"{'':>3}  ↳ {_tronquer(analyse['resume'], 100)}")
        if moisissure:
            alertes.append(a)
    if alertes:
        print()
        print(f"⚠  {len(alertes)} annonce(s) avec moisissure détectée sur les photos :")
        for a in alertes:
            pts = ", ".join((a.get("analyse_photo") or {}).get("points_negatifs") or [])
            print(f"   - {_tronquer(a.get('titre'), 50)} — {a['url']}")
            if pts:
                print(f"     {_tronquer(pts, 100)}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parser_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scraping + notation d'annonces immobilières.")
    p.add_argument("--skip-photos", action="store_true",
                   help="ne pas lancer l'analyse photo (passage rapide prix/trajet/surface)")
    p.add_argument("--analyser-manquantes", action="store_true",
                   help="analyser les photos des annonces déjà en base mais jamais analysées")
    p.add_argument("--recalculer", action="store_true",
                   help="recalculer tous les scores avec la pondération actuelle")
    p.add_argument("--max-photos", type=int, default=None,
                   help=f"photos max par annonce (défaut : {config.MAX_PHOTOS_PAR_ANNONCE})")
    p.add_argument("--sources", default=None,
                   help="sources à scraper, séparées par des virgules (défaut : SOURCES_ACTIVES)")
    p.add_argument("--no-scrape", action="store_true",
                   help="ne pas scraper, afficher seulement le classement de la base")
    p.add_argument("--limite", type=int, default=30, help="nombre d'annonces affichées")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parser_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    sources = [s.strip() for s in (args.sources or ",".join(config.SOURCES_ACTIVES)).split(",") if s.strip()]
    inconnues = [s for s in sources if s not in SCRAPERS]
    if inconnues and not args.no_scrape:
        print(f"Sources inconnues : {', '.join(inconnues)}. Disponibles : {', '.join(SCRAPERS)}",
              file=sys.stderr)
        return 2

    conn = db.init_db()
    if not args.no_scrape:
        executer("rapide" if args.skip_photos else "complet", sources=sources,
                 max_photos=args.max_photos, conn=conn)
    if args.analyser_manquantes and not args.skip_photos:
        executer("manquantes", max_photos=args.max_photos, conn=conn)
    if args.recalculer:
        executer("recalculer", conn=conn)

    afficher_classement(db.lister_annonces(conn), limite=args.limite)
    return 0


if __name__ == "__main__":
    sys.exit(main())
