import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import config
import photo_analysis as pa

FAUSSE_IMAGE = ("image/jpeg", b"\xff\xd8fake")
DEUX_PHOTOS = ["https://ex.com/a.jpg", "https://ex.com/b.jpg"]
PAYLOAD_OK = {"score_etat": 42.0, "moisissure_detectee": True,
              "points_negatifs": ["auréoles"], "resume": "Humidité visible"}


# --- commun ---------------------------------------------------------------
def test_pas_de_photos_est_neutre():
    r = pa.analyser_photos([], backend="api", client=MagicMock())
    assert r["statut"] == "neutre"
    assert r["score_etat"] == config.PHOTO_SCORE_NEUTRE
    assert r["moisissure_detectee"] is False


def test_photos_non_telechargeables_est_neutre():
    client = MagicMock()
    with patch.object(pa, "telecharger_photo", return_value=None):
        r = pa.analyser_photos(DEUX_PHOTOS, backend="api", client=client)
    assert r["statut"] == "neutre"
    client.messages.create.assert_not_called()


def test_backend_inconnu():
    with patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE), pytest.raises(ValueError):
        pa.analyser_photos(DEUX_PHOTOS, backend="autre")


# --- backend claude_code (CLI) ---------------------------------------------
def _proc(stdout: str, code: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


def _enveloppe(payload, **extra):
    env = {"is_error": False, "result": json.dumps(payload), "structured_output": payload}
    env.update(extra)
    return json.dumps(env)


def test_claude_code_ok_et_commande():
    photos = [f"https://ex.com/{i}.jpg" for i in range(10)]
    with patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE) as dl, \
         patch.object(pa.subprocess, "run", return_value=_proc(_enveloppe(PAYLOAD_OK))) as run:
        r = pa.analyser_photos(photos, titre="T2", backend="claude_code")
    assert dl.call_count == config.MAX_PHOTOS_PAR_ANNONCE
    assert r["statut"] == "ok" and r["score_etat"] == 42 and r["moisissure_detectee"] is True
    assert r["nb_photos_analysees"] == config.MAX_PHOTOS_PAR_ANNONCE

    cmd = run.call_args.args[0]
    assert cmd[0] == config.PHOTO_CLAUDE_CLI and cmd[1] == "-p"
    assert "--json-schema" in cmd and "--output-format" in cmd and "--no-session-persistence" in cmd
    assert cmd[cmd.index("--model") + 1] == config.PHOTO_CLAUDE_MODEL
    assert cmd[cmd.index("--allowedTools") + 1] == "Read"
    assert cmd[2].count("photo_") == config.MAX_PHOTOS_PAR_ANNONCE  # chemins listés dans le prompt
    assert run.call_args.kwargs["stdin"] is subprocess.DEVNULL


def test_claude_code_sans_structured_output_parse_le_texte():
    env = json.dumps({"is_error": False, "result": "Voici :\n```json\n" + json.dumps(PAYLOAD_OK) + "\n```"})
    with patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE), \
         patch.object(pa.subprocess, "run", return_value=_proc(env)):
        r = pa.analyser_photos(DEUX_PHOTOS, backend="claude_code")
    assert r["statut"] == "ok" and r["score_etat"] == 42


def test_claude_code_echec_est_erreur():
    with patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE), \
         patch.object(pa.subprocess, "run", return_value=_proc("", code=1, stderr="boom")):
        r = pa.analyser_photos(DEUX_PHOTOS, backend="claude_code")
    assert r["statut"] == "erreur"


def test_claude_code_cli_absent_est_erreur():
    with patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE), \
         patch.object(pa.subprocess, "run", side_effect=FileNotFoundError):
        r = pa.analyser_photos(DEUX_PHOTOS, backend="claude_code")
    assert r["statut"] == "erreur"


def test_claude_code_limite_debit_leve():
    env = json.dumps({"is_error": True, "result": "You've hit your usage limit"})
    with patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE), \
         patch.object(pa.subprocess, "run", return_value=_proc(env)), pytest.raises(pa.LimiteDebit):
        pa.analyser_photos(DEUX_PHOTOS, backend="claude_code")


# --- backend api (SDK) -----------------------------------------------------
def _client(payload, stop_reason="end_turn"):
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(payload))])
    return client


def test_api_ok_et_requete():
    client = _client(PAYLOAD_OK)
    with patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE):
        r = pa.analyser_photos(DEUX_PHOTOS, titre="T2", backend="api", client=client)
    assert r["statut"] == "ok" and r["score_etat"] == 42
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == config.PHOTO_MODEL
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    contenu = kwargs["messages"][0]["content"]
    assert sum(1 for b in contenu if b["type"] == "image") == 2
    assert contenu[0]["source"]["type"] == "base64" and contenu[-1]["type"] == "text"


def test_api_refus_du_modele_est_neutre():
    with patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE):
        r = pa.analyser_photos(DEUX_PHOTOS, backend="api", client=_client({}, stop_reason="refusal"))
    assert r["statut"] == "neutre"


def test_api_json_invalide_est_erreur():
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="pas du json")])
    with patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE):
        r = pa.analyser_photos(DEUX_PHOTOS, backend="api", client=client)
    assert r["statut"] == "erreur"


# --- lots ----------------------------------------------------------------
def test_batch_appelle_callback_et_throttle():
    annonces = [{"url": f"u{i}", "titre": f"a{i}", "photos": DEUX_PHOTOS} for i in range(3)]
    vus = []
    payload = {"score_etat": 75, "moisissure_detectee": False, "points_negatifs": [], "resume": "ok"}
    with patch.object(config, "PHOTO_BACKEND", "claude_code"), \
         patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE), \
         patch.object(pa.subprocess, "run", return_value=_proc(_enveloppe(payload))), \
         patch.object(pa.time, "sleep") as sleep:
        pa.analyser_annonces(annonces, apres_chaque=lambda a: vus.append(a["url"]))
    assert vus == ["u0", "u1", "u2"]
    assert all(a["analyse_photo"]["score_etat"] == 75 for a in annonces)
    assert sleep.call_count == 2  # pause entre les appels, pas après le dernier


def test_batch_reprise_apres_limite_debit():
    annonces = [{"url": "u0", "titre": "a", "photos": DEUX_PHOTOS}]
    limite = json.dumps({"is_error": True, "result": "rate limit"})
    payload = {"score_etat": 70, "moisissure_detectee": False, "points_negatifs": [], "resume": "ok"}
    with patch.object(config, "PHOTO_BACKEND", "claude_code"), \
         patch.object(pa, "telecharger_photo", return_value=FAUSSE_IMAGE), \
         patch.object(pa.subprocess, "run", side_effect=[_proc(limite), _proc(_enveloppe(payload))]), \
         patch.object(pa.time, "sleep") as sleep:
        pa.analyser_annonces(annonces)
    assert annonces[0]["analyse_photo"]["statut"] == "ok"
    assert sleep.call_count == 1
