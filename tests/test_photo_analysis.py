import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import config
import photo_analysis as pa


def _reponse(payload: dict, stop_reason="end_turn"):
    bloc = SimpleNamespace(type="text", text=json.dumps(payload))
    return SimpleNamespace(stop_reason=stop_reason, content=[bloc])


def _client(payload, stop_reason="end_turn"):
    client = MagicMock()
    client.messages.create.return_value = _reponse(payload, stop_reason)
    return client


FAUSSE_IMAGE = ("image/jpeg", "AAAA")


def test_pas_de_photos_est_neutre():
    r = pa.analyser_photos([], client=MagicMock())
    assert r["statut"] == "neutre"
    assert r["score_etat"] == config.PHOTO_SCORE_NEUTRE
    assert r["moisissure_detectee"] is False


def test_analyse_ok_et_limite_photos():
    client = _client({"score_etat": 42.0, "moisissure_detectee": True,
                      "points_negatifs": ["auréoles"], "resume": "Humidité visible"})
    photos = [f"https://ex.com/{i}.jpg" for i in range(10)]
    with patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE) as dl:
        r = pa.analyser_photos(photos, titre="T2", client=client)
    assert dl.call_count == config.MAX_PHOTOS_PAR_ANNONCE
    assert r["statut"] == "ok" and r["score_etat"] == 42 and r["moisissure_detectee"] is True
    assert r["nb_photos_analysees"] == config.MAX_PHOTOS_PAR_ANNONCE

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == config.PHOTO_MODEL
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    contenu = kwargs["messages"][0]["content"]
    assert sum(1 for b in contenu if b["type"] == "image") == config.MAX_PHOTOS_PAR_ANNONCE
    assert contenu[-1]["type"] == "text"


def test_photos_non_telechargeables_est_neutre():
    client = _client({})
    with patch.object(pa, "telecharger_photo", return_value=None):
        r = pa.analyser_photos(["https://ex.com/a.jpg", "https://ex.com/b.jpg"], client=client)
    assert r["statut"] == "neutre"
    client.messages.create.assert_not_called()


def test_refus_du_modele_est_neutre():
    client = _client({}, stop_reason="refusal")
    with patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE):
        r = pa.analyser_photos(["https://ex.com/a.jpg", "https://ex.com/b.jpg"], client=client)
    assert r["statut"] == "neutre"


def test_json_invalide_est_erreur():
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="pas du json")])
    with patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE):
        r = pa.analyser_photos(["https://ex.com/a.jpg", "https://ex.com/b.jpg"], client=client)
    assert r["statut"] == "erreur"


def test_batch_appelle_callback_et_throttle():
    client = _client({"score_etat": 75, "moisissure_detectee": False, "points_negatifs": [], "resume": "ok"})
    annonces = [{"url": f"u{i}", "titre": f"a{i}", "photos": ["https://ex.com/1.jpg", "https://ex.com/2.jpg"]}
                for i in range(3)]
    vus = []
    with patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE), \
         patch.object(pa.time, "sleep") as sleep:
        pa.analyser_annonces(annonces, client=client, apres_chaque=lambda a: vus.append(a["url"]))
    assert vus == ["u0", "u1", "u2"]
    assert all(a["analyse_photo"]["score_etat"] == 75 for a in annonces)
    assert sleep.call_count == 2  # pause entre les appels, pas après le dernier
