"""Interface web locale : annonces, fiche détaillée, paramètres, lancement des traitements.

Lancement : `python app.py` (ouvre le navigateur sur http://127.0.0.1:5000).
"""

import logging
import threading
import webbrowser

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, url_for

import config
import db
import jobs
import main as pipeline
import photo_analysis
import scoring
from scrapers import SCRAPERS

app = Flask(__name__)
app.secret_key = "scrapper-annonces-local"   # usage local uniquement (messages flash)
app.jinja_env.trim_blocks = True
app.jinja_env.lstrip_blocks = True


# ---------------------------------------------------------------------------
# Connexion par requête
# ---------------------------------------------------------------------------
def get_conn():
    if "conn" not in g:
        g.conn = db.init_db()
    return g.conn


@app.teardown_appcontext
def fermer_conn(_exc):
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


@app.context_processor
def contexte_global():
    return {
        "job_en_cours": jobs.en_cours(),
        "sources_disponibles": list(SCRAPERS),
        "modes": pipeline.MODES,
    }


@app.template_filter("euros")
def filtre_euros(valeur):
    return f"{int(valeur):,}".replace(",", " ") + " €" if valeur is not None else "?"


@app.template_filter("date_courte")
def filtre_date(valeur):
    return (valeur or "")[:16].replace("T", " ")


# ---------------------------------------------------------------------------
# Annonces
# ---------------------------------------------------------------------------
def _bool_param(nom: str):
    valeur = request.args.get(nom)
    if valeur in ("1", "oui", "true"):
        return True
    if valeur in ("0", "non", "false"):
        return False
    return None


@app.route("/")
def annonces():
    conn = get_conn()
    filtres = {
        "source": request.args.get("source") or None,
        "ville": request.args.get("ville") or None,
        "moisissure": _bool_param("moisissure"),
        "favoris": request.args.get("favoris") == "1",
        "masquees": request.args.get("masquees") == "1",
        "q": request.args.get("q") or None,
        "score_min": _float_ou_none(request.args.get("score_min")),
        "tri": request.args.get("tri") or "score",
    }
    liste = db.rechercher_annonces(conn, **filtres)
    return render_template(
        "annonces.html",
        annonces=liste,
        filtres=filtres,
        stats=db.statistiques(conn),
        distinctes=db.valeurs_distinctes(conn),
        tris=db.TRIS,
    )


@app.route("/annonce/<int:annonce_id>")
def annonce(annonce_id: int):
    conn = get_conn()
    a = db.get_annonce(conn, annonce_id)
    if a is None:
        abort(404)
    analyse = a.get("analyse_photo") or {"score_etat": None, "moisissure_detectee": False,
                                        "points_negatifs": [], "resume": None,
                                        "nb_photos_analysees": None, "analyse_le": None}
    sous_scores = {
        "Trajet": (scoring.score_trajet(a["ville"]), config.POIDS_TRAJET),
        "Prix": (scoring.score_prix(a["prix"]), config.POIDS_PRIX),
        "Surface": (scoring.score_surface(a["surface"]), config.POIDS_SURFACE),
        "État (photos)": (analyse.get("score_etat") if analyse.get("score_etat") is not None
                          else config.PHOTO_SCORE_NEUTRE, config.POIDS_PHOTO),
    }
    return render_template("annonce.html", a=a, analyse=analyse, sous_scores=sous_scores,
                           trajet_minutes=config.TRAJET_MINUTES.get(a["ville"], config.TRAJET_DEFAUT))


@app.post("/annonce/<int:annonce_id>/<action>")
def action_annonce(annonce_id: int, action: str):
    conn = get_conn()
    a = db.get_annonce(conn, annonce_id)
    if a is None:
        abort(404)
    if action == "favori":
        db.definir_drapeau(conn, annonce_id, "favori", not a["favori"])
    elif action == "masquer":
        db.definir_drapeau(conn, annonce_id, "masquee", not a["masquee"])
    elif action == "reanalyser":
        db.reinitialiser_analyse(conn, annonce_id)
        db.maj_score(conn, a["url"], scoring.calculer_score(a["prix"], a["surface"], a["ville"]))
    elif action == "notes":
        db.definir_notes(conn, annonce_id, request.form.get("notes", ""))
    elif action == "supprimer":
        db.supprimer_annonce(conn, annonce_id)
    else:
        abort(400)

    if request.is_json or request.headers.get("X-Requested-With") == "fetch":
        a = db.get_annonce(conn, annonce_id)
        return jsonify({"ok": True, "favori": bool(a and a["favori"]),
                        "masquee": bool(a and a["masquee"]), "supprimee": a is None})
    if action == "supprimer":
        return redirect(url_for("annonces"))
    return redirect(request.referrer or url_for("annonce", annonce_id=annonce_id))


# ---------------------------------------------------------------------------
# Paramètres
# ---------------------------------------------------------------------------
def _float_ou_none(texte):
    try:
        return float(texte) if texte not in (None, "") else None
    except ValueError:
        return None


def parser_formulaire(form) -> tuple[dict, list[str]]:
    """Convertit le formulaire en valeurs typées selon config.PARAMETRES. Renvoie (valeurs, erreurs)."""
    valeurs, erreurs = {}, []
    for nom, type_, _section, libelle, _aide in config.PARAMETRES:
        brut = form.get(nom, "")
        try:
            if type_ == "int":
                valeurs[nom] = int(float(brut))
            elif type_ == "float":
                valeurs[nom] = float(brut)
            elif type_ == "str":
                valeurs[nom] = brut.strip()
            elif type_ == "liste":
                valeurs[nom] = [v.strip() for v in brut.split(",") if v.strip()]
            elif type_ == "sources":
                valeurs[nom] = [s for s in form.getlist(nom) if s in SCRAPERS]
            elif type_ == "dict_int":
                table = {}
                for ligne in brut.splitlines():
                    if not ligne.strip():
                        continue
                    if ":" not in ligne:
                        raise ValueError(f"ligne « {ligne.strip()} » sans « : »")
                    ville, minutes = ligne.split(":", 1)
                    table[ville.strip()] = int(float(minutes.strip()))
                valeurs[nom] = table
            elif type_.startswith("choix:"):
                choix = type_.split(":", 1)[1].split("|")
                if brut not in choix:
                    raise ValueError(f"valeur attendue parmi {', '.join(choix)}")
                valeurs[nom] = brut
        except ValueError as exc:
            erreurs.append(f"{libelle} : {exc}")

    if not erreurs:
        somme = sum(valeurs[n] for n in ("POIDS_TRAJET", "POIDS_PRIX", "POIDS_SURFACE", "POIDS_PHOTO"))
        if abs(somme - 1.0) > 0.001:
            erreurs.append(f"La somme des poids doit faire 1 (actuellement {somme:.3f}).")
        if any(valeurs[n] < 0 for n in ("POIDS_TRAJET", "POIDS_PRIX", "POIDS_SURFACE", "POIDS_PHOTO")):
            erreurs.append("Les poids doivent être positifs.")
        if not valeurs["VILLES"]:
            erreurs.append("Indiquez au moins une ville.")
        if not valeurs["SOURCES_ACTIVES"]:
            erreurs.append("Cochez au moins un site.")
    return valeurs, erreurs


@app.route("/parametres", methods=["GET", "POST"])
def parametres():
    if request.method == "POST":
        valeurs, erreurs = parser_formulaire(request.form)
        if erreurs:
            for e in erreurs:
                flash(e, "erreur")
            return render_template("parametres.html", valeurs=_valeurs_formulaire(valeurs),
                                   sections=_sections(), cli_ok=photo_analysis.cli_disponible()), 400
        config.enregistrer(valeurs)
        flash("Paramètres enregistrés. Pensez à « Recalculer les scores » si vous avez modifié la pondération.", "ok")
        return redirect(url_for("parametres"))
    return render_template("parametres.html", valeurs=_valeurs_formulaire(config.exporter()),
                           sections=_sections(), cli_ok=photo_analysis.cli_disponible())


def _sections() -> list[tuple[str, list]]:
    sections: dict[str, list] = {}
    for nom, type_, section, libelle, aide in config.PARAMETRES:
        sections.setdefault(section, []).append((nom, type_, libelle, aide))
    return list(sections.items())


def _valeurs_formulaire(valeurs: dict) -> dict:
    """Valeurs prêtes pour les champs du formulaire (listes et tables -> texte)."""
    resultat = {}
    for nom, type_, *_ in config.PARAMETRES:
        v = valeurs.get(nom, config.exporter().get(nom))
        if type_ == "liste":
            v = ", ".join(v)
        elif type_ == "dict_int":
            v = "\n".join(f"{k}: {val}" for k, val in v.items())
        resultat[nom] = v
    return resultat


# ---------------------------------------------------------------------------
# Lancement des traitements
# ---------------------------------------------------------------------------
@app.route("/lancer")
def lancer():
    return render_template("lancer.html", job=jobs.courant(), historique=jobs.historique(),
                           cli_ok=photo_analysis.cli_disponible())


@app.post("/api/lancer")
def api_lancer():
    donnees = request.get_json(silent=True) or request.form
    mode = donnees.get("mode", "complet")
    if mode not in pipeline.MODES:
        return jsonify({"ok": False, "erreur": f"Mode inconnu : {mode}"}), 400
    try:
        job = jobs.lancer(mode)
    except RuntimeError as exc:
        return jsonify({"ok": False, "erreur": str(exc)}), 409
    return jsonify({"ok": True, "job": job.etat()})


@app.get("/api/job")
def api_job():
    job = jobs.courant()
    if job is None:
        return jsonify({"job": None})
    depuis = request.args.get("depuis", 0, type=int)
    return jsonify({"job": job.etat(depuis)})


@app.get("/api/stats")
def api_stats():
    return jsonify(db.statistiques(get_conn()))


# ---------------------------------------------------------------------------
def demarrer(ouvrir_navigateur: bool = True) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    url = f"http://{config.WEB_HOTE}:{config.WEB_PORT}/"
    print(f"Interface disponible sur {url}  (Ctrl+C pour arrêter)")
    if ouvrir_navigateur:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=config.WEB_HOTE, port=config.WEB_PORT, debug=False, threaded=True)


if __name__ == "__main__":
    demarrer()
