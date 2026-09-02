import config
import scoring


def test_poids_somme_1():
    total = config.POIDS_TRAJET + config.POIDS_PRIX + config.POIDS_SURFACE + config.POIDS_PHOTO
    assert abs(total - 1.0) < 1e-9


def test_score_dans_bornes():
    s = scoring.calculer_score(1000, 45, "Paris", score_etat=80)
    assert 0 <= s <= 100


def test_sans_analyse_photo_est_neutre():
    neutre = scoring.calculer_score(1000, 45, "Paris")
    explicite = scoring.calculer_score(1000, 45, "Paris", score_etat=config.PHOTO_SCORE_NEUTRE)
    assert neutre == explicite


def test_etat_influence_le_score():
    assert scoring.calculer_score(1000, 45, "Paris", score_etat=90) > \
        scoring.calculer_score(1000, 45, "Paris", score_etat=30)


def test_moisissure_plafonne():
    sans = scoring.calculer_score(800, 60, "Paris", score_etat=70)
    avec = scoring.calculer_score(800, 60, "Paris", score_etat=70, moisissure_detectee=True)
    assert avec <= config.PLAFOND_SCORE_MOISISSURE
    assert avec <= sans - config.MALUS_MOISISSURE or avec == config.PLAFOND_SCORE_MOISISSURE


def test_calculer_score_annonce():
    a = {"prix": 1000, "surface": 45, "ville": "Paris",
         "analyse_photo": {"score_etat": 20, "moisissure_detectee": True}}
    assert scoring.calculer_score_annonce(a) <= config.PLAFOND_SCORE_MOISISSURE
