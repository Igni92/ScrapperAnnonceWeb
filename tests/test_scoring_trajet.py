import config
import scoring


def test_trajet_reel_prime_sur_la_table():
    court = scoring.calculer_score(1000, 45, "Paris", trajet_minutes=10)
    long_ = scoring.calculer_score(1000, 45, "Paris", trajet_minutes=55)
    table = scoring.calculer_score(1000, 45, "Paris")
    assert court > table > long_


def test_minutes_trajet_secours():
    assert scoring.minutes_trajet("Paris") == config.TRAJET_MINUTES["Paris"]
    assert scoring.minutes_trajet("Inconnue") == config.TRAJET_DEFAUT
    assert scoring.minutes_trajet("Paris", 12) == 12


def test_calculer_score_annonce_utilise_trajet_minutes():
    a = {"prix": 1000, "surface": 45, "ville": "Paris", "trajet_minutes": 5}
    b = {"prix": 1000, "surface": 45, "ville": "Paris", "trajet_minutes": 59}
    assert scoring.calculer_score_annonce(a) > scoring.calculer_score_annonce(b)
