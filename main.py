"""Orchestration : scraping -> filtrage -> analyse photo (nouvelles annonces) -> scoring -> DB -> classement."""

import argparse
import logging
import sys

import config
import db
import photo_analysis
import scoring
from scrapers import SCRAPERS

log = logging.getLogger("main")


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
            # (--analyser-manquantes), sans jamais mettre en cache un résultat bidon.
            annonce["analyse_photo"] = None
        annonce["score"] = scoring.calculer_score_annonce(annonce)
        db.inserer_annonce(conn, annonce)

    photo_analysis.analyser_annonces(nouvelles, max_photos=max_photos, apres_chaque=persister)


def analyser_manquantes(conn, max_photos: int | None) -> None:
    """Rattrape les annonces en base sans analyse (insérées lors d'un run --skip-photos ou en erreur)."""
    manquantes = db.annonces_sans_analyse(conn)
    if not manquantes:
        log.info("Aucune annonce en attente d'analyse photo.")
        return
    log.info("Rattrapage : %d annonce(s) en base sans analyse photo", len(manquantes))

    def persister(annonce: dict) -> None:
        analyse = annonce.get("analyse_photo") or {}
        if analyse.get("statut") == "erreur":
            return
        score = scoring.calculer_score_annonce(annonce)
        annonce["score"] = score
        db.maj_analyse_photo(conn, annonce["url"], analyse, score=score)

    photo_analysis.analyser_annonces(manquantes, max_photos=max_photos, apres_chaque=persister)


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------
def _tronquer(texte: str, longueur: int) -> str:
    texte = texte or ""
    return texte if len(texte) <= longueur else texte[: longueur - 1] + "…"


def afficher_classement(annonces: list[dict], limite: int = 30) -> None:
    classees = sorted(annonces, key=lambda a: (a.get("score") is None, -(a.get("score") or 0)))
    print()
    print(f"{'#':>3}  {'Score':>5}  {'État':>5}  {'Prix':>6}  {'Surf.':>6}  {'P.':>2}  "
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
        print(
            f"{rang:>3}  {score if score is not None else '?':>5}  {etat_txt}  "
            f"{int(a['prix']) if a.get('prix') else '?':>6}  "
            f"{a['surface'] if a.get('surface') else '?':>6}  "
            f"{a['pieces'] if a.get('pieces') else '?':>2}  "
            f"{_tronquer(a.get('ville'), 14):<14} {a['source']:<9} "
            f"{_tronquer(a.get('titre'), 40)}{drapeau}"
        )
        print(f"{'':>3}  {a['url']}")
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
    p.add_argument("--max-photos", type=int, default=None,
                   help=f"photos max par annonce (défaut : {config.MAX_PHOTOS_PAR_ANNONCE})")
    p.add_argument("--sources", default=",".join(SCRAPERS),
                   help="sources à scraper, séparées par des virgules (défaut : toutes)")
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
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    inconnues = [s for s in sources if s not in SCRAPERS]
    if inconnues:
        print(f"Sources inconnues : {', '.join(inconnues)}. Disponibles : {', '.join(SCRAPERS)}",
              file=sys.stderr)
        return 2

    conn = db.init_db()

    if not args.no_scrape:
        brutes = scraper_tout(sources)
        candidates = dedupliquer(filtrer(brutes))
        log.info("%d annonce(s) après filtrage (%d brutes)", len(candidates), len(brutes))

        connues = db.urls_connues(conn, (a["url"] for a in candidates))
        nouvelles = [a for a in candidates if a["url"] not in connues]
        log.info("%d nouvelle(s) annonce(s), %d déjà en base", len(nouvelles), len(connues))

        if args.skip_photos:
            for a in nouvelles:
                a["score"] = scoring.calculer_score_annonce(a)
                db.inserer_annonce(conn, a)
        else:
            analyser_nouvelles(conn, nouvelles, args.max_photos)

    if args.analyser_manquantes and not args.skip_photos:
        analyser_manquantes(conn, args.max_photos)

    afficher_classement(db.lister_annonces(conn), limite=args.limite)
    return 0


if __name__ == "__main__":
    sys.exit(main())
