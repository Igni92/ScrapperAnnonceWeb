"""Exécution en arrière-plan d'un traitement (scraping / analyse) pour l'interface web.

Un seul traitement à la fois ; les lignes de log sont capturées pour affichage en direct.
"""

import logging
import threading
import time
import traceback

import main

_verrou = threading.Lock()
_courant: "Job | None" = None
_historique: list["Job"] = []


class _CaptureLog(logging.Handler):
    def __init__(self, job: "Job"):
        super().__init__(level=logging.INFO)
        self.job = job
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("werkzeug"):   # journal d'accès HTTP du serveur web : sans intérêt ici
            return
        self.job.lignes.append(self.format(record))


class Job:
    def __init__(self, mode: str, **options):
        self.id = int(time.time() * 1000)
        self.mode = mode
        self.options = options
        self.statut = "en_attente"   # en_attente | en_cours | termine | erreur
        self.lignes: list[str] = []
        self.debut = time.time()
        self.fin: float | None = None
        self.resume: dict | None = None
        self.erreur: str | None = None

    def executer(self) -> None:
        handler = _CaptureLog(self)
        racine = logging.getLogger()
        niveau_initial = racine.level
        racine.addHandler(handler)
        if racine.level > logging.INFO or racine.level == logging.NOTSET:
            racine.setLevel(logging.INFO)
        self.statut = "en_cours"
        try:
            self.resume = main.executer(self.mode, **self.options)
            self.statut = "termine"
        except Exception as exc:  # affiché dans l'interface, ne doit pas tuer le serveur
            self.statut = "erreur"
            self.erreur = f"{type(exc).__name__}: {exc}"
            self.lignes.append("ERREUR " + traceback.format_exc().strip())
        finally:
            self.fin = time.time()
            racine.removeHandler(handler)
            racine.setLevel(niveau_initial)

    def etat(self, depuis: int = 0) -> dict:
        return {
            "id": self.id,
            "mode": self.mode,
            "libelle": main.MODES.get(self.mode, self.mode),
            "statut": self.statut,
            "lignes": self.lignes[depuis:],
            "total_lignes": len(self.lignes),
            "duree": round((self.fin or time.time()) - self.debut),
            "resume": self.resume,
            "erreur": self.erreur,
        }


def en_cours() -> bool:
    return _courant is not None and _courant.statut in ("en_attente", "en_cours")


def courant() -> "Job | None":
    return _courant


def lancer(mode: str, **options) -> Job:
    """Démarre un traitement en arrière-plan. Lève RuntimeError si un autre est en cours."""
    global _courant
    with _verrou:
        if en_cours():
            raise RuntimeError("Un traitement est déjà en cours.")
        job = Job(mode, **options)
        _courant = job
        _historique.append(job)
        del _historique[:-10]
    threading.Thread(target=job.executer, name=f"job-{mode}", daemon=True).start()
    return job


def historique() -> list[Job]:
    return list(reversed(_historique))
