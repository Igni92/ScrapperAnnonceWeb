"""Notation des annonces : score 0-100 pondéré (trajet, prix, surface, état du logement)."""

import config


def _borner(valeur: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, valeur))


def _interpoler(valeur: float, bas: float, haut: float, score_bas: float, score_haut: float) -> float:
    """Interpolation linéaire bornée de `valeur` entre (bas -> score_bas) et (haut -> score_haut)."""
    if haut == bas:
        return score_haut
    ratio = (valeur - bas) / (haut - bas)
    return _borner(score_bas + ratio * (score_haut - score_bas))


def score_prix(prix: float | None) -> float:
    if prix is None:
        return 50.0
    return _interpoler(prix, config.PRIX_MIN_REF, config.PRIX_MAX, 100.0, 0.0)


def score_surface(surface: float | None) -> float:
    if surface is None:
        return 50.0
    return _interpoler(surface, config.SURFACE_MIN, config.SURFACE_MAX_REF, 0.0, 100.0)


def score_trajet(ville: str | None) -> float:
    minutes = config.TRAJET_MINUTES.get(ville, config.TRAJET_DEFAUT) if ville else config.TRAJET_DEFAUT
    return _interpoler(minutes, 0, config.TRAJET_MAX_REF, 100.0, 0.0)


def calculer_score(prix, surface, ville, score_etat=None, moisissure_detectee=False) -> float:
    """Score global 0-100.

    - `score_etat` : score 0-100 issu de l'analyse photo. None => score neutre
      (config.PHOTO_SCORE_NEUTRE) : une annonce sans photos n'est pas pénalisée.
    - `moisissure_detectee` : applique un malus fixe (MALUS_MOISISSURE) puis plafonne
      le score à PLAFOND_SCORE_MOISISSURE, pour que ces annonces tombent en bas du
      classement quelle que soit leur qualité par ailleurs.
    """
    etat = config.PHOTO_SCORE_NEUTRE if score_etat is None else _borner(float(score_etat))

    score = (
        config.POIDS_TRAJET * score_trajet(ville)
        + config.POIDS_PRIX * score_prix(prix)
        + config.POIDS_SURFACE * score_surface(surface)
        + config.POIDS_PHOTO * etat
    )

    if moisissure_detectee:
        score = min(score - config.MALUS_MOISISSURE, config.PLAFOND_SCORE_MOISISSURE)

    return round(_borner(score), 1)


def calculer_score_annonce(annonce: dict) -> float:
    """Variante pratique : lit prix/surface/ville/analyse_photo directement dans le dict."""
    analyse = annonce.get("analyse_photo") or {}
    return calculer_score(
        annonce.get("prix"),
        annonce.get("surface"),
        annonce.get("ville"),
        score_etat=analyse.get("score_etat"),
        moisissure_detectee=bool(analyse.get("moisissure_detectee")),
    )
