"""Stockage SQLite des annonces + cache des analyses photo (clé : URL de l'annonce)."""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterable

import config

# Colonnes ajoutées après la création initiale de la table : ajoutées à la volée
# par init_db() si elles manquent (migration légère).
COLONNES_SUPPLEMENTAIRES = {
    "photos": "TEXT",                  # JSON : liste d'URLs
    "score_etat": "INTEGER",           # 0-100, NULL si pas encore analysé
    "moisissure_detectee": "INTEGER",  # 0/1, NULL si pas encore analysé
    "points_negatifs": "TEXT",         # JSON : liste de chaînes
    "resume_etat": "TEXT",
    "nb_photos_analysees": "INTEGER",
    "photo_analyse_le": "TEXT",        # ISO 8601, NULL si pas encore analysé
}


def connexion(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | None = None) -> sqlite3.Connection:
    conn = connexion(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS annonces (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            source    TEXT NOT NULL,
            titre     TEXT,
            ville     TEXT,
            prix      REAL,
            surface   REAL,
            pieces    INTEGER,
            url       TEXT NOT NULL UNIQUE,
            score     REAL,
            cree_le   TEXT NOT NULL
        )
        """
    )
    existantes = {row["name"] for row in conn.execute("PRAGMA table_info(annonces)")}
    for nom, type_sql in COLONNES_SUPPLEMENTAIRES.items():
        if nom not in existantes:
            conn.execute(f"ALTER TABLE annonces ADD COLUMN {nom} {type_sql}")
    conn.commit()
    return conn


def _maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def annonce_existe(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute("SELECT 1 FROM annonces WHERE url = ?", (url,)).fetchone()
    return row is not None


def urls_connues(conn: sqlite3.Connection, urls: Iterable[str]) -> set[str]:
    """Retourne le sous-ensemble des URLs déjà présentes en base."""
    urls = list(urls)
    connues: set[str] = set()
    # SQLite limite le nombre de paramètres par requête : on découpe.
    for i in range(0, len(urls), 500):
        lot = urls[i : i + 500]
        marques = ",".join("?" * len(lot))
        rows = conn.execute(f"SELECT url FROM annonces WHERE url IN ({marques})", lot)
        connues.update(r["url"] for r in rows)
    return connues


def inserer_annonce(conn: sqlite3.Connection, annonce: dict) -> None:
    """Insère une annonce (ignorée silencieusement si l'URL existe déjà)."""
    analyse = annonce.get("analyse_photo") or {}
    conn.execute(
        """
        INSERT OR IGNORE INTO annonces
            (source, titre, ville, prix, surface, pieces, url, score, cree_le,
             photos, score_etat, moisissure_detectee, points_negatifs, resume_etat,
             nb_photos_analysees, photo_analyse_le)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            annonce["source"],
            annonce.get("titre"),
            annonce.get("ville"),
            annonce.get("prix"),
            annonce.get("surface"),
            annonce.get("pieces"),
            annonce["url"],
            annonce.get("score"),
            _maintenant(),
            json.dumps(annonce.get("photos") or [], ensure_ascii=False),
            analyse.get("score_etat"),
            _bool_vers_int(analyse.get("moisissure_detectee")),
            json.dumps(analyse.get("points_negatifs") or [], ensure_ascii=False)
            if analyse else None,
            analyse.get("resume"),
            analyse.get("nb_photos_analysees"),
            _maintenant() if analyse else None,
        ),
    )
    conn.commit()


def maj_analyse_photo(conn: sqlite3.Connection, url: str, analyse: dict,
                      score: float | None = None) -> None:
    """Enregistre (ou remplace) le résultat d'analyse photo d'une annonce déjà en base."""
    conn.execute(
        """
        UPDATE annonces
           SET score_etat = ?, moisissure_detectee = ?, points_negatifs = ?,
               resume_etat = ?, nb_photos_analysees = ?, photo_analyse_le = ?,
               score = COALESCE(?, score)
         WHERE url = ?
        """,
        (
            analyse.get("score_etat"),
            _bool_vers_int(analyse.get("moisissure_detectee")),
            json.dumps(analyse.get("points_negatifs") or [], ensure_ascii=False),
            analyse.get("resume"),
            analyse.get("nb_photos_analysees"),
            _maintenant(),
            score,
            url,
        ),
    )
    conn.commit()


def maj_score(conn: sqlite3.Connection, url: str, score: float) -> None:
    conn.execute("UPDATE annonces SET score = ? WHERE url = ?", (score, url))
    conn.commit()


def get_analyse_photo(conn: sqlite3.Connection, url: str) -> dict | None:
    """Retourne l'analyse photo en cache pour cette URL, ou None si jamais analysée."""
    row = conn.execute(
        """
        SELECT score_etat, moisissure_detectee, points_negatifs, resume_etat,
               nb_photos_analysees, photo_analyse_le
          FROM annonces WHERE url = ?
        """,
        (url,),
    ).fetchone()
    if row is None or row["photo_analyse_le"] is None:
        return None
    return _analyse_depuis_row(row)


def annonces_sans_analyse(conn: sqlite3.Connection) -> list[dict]:
    """Annonces en base jamais passées par l'analyse photo (ex. run en --skip-photos)."""
    rows = conn.execute(
        "SELECT * FROM annonces WHERE photo_analyse_le IS NULL ORDER BY id"
    ).fetchall()
    return [_annonce_depuis_row(r) for r in rows]


def lister_annonces(conn: sqlite3.Connection, limite: int | None = None) -> list[dict]:
    sql = "SELECT * FROM annonces ORDER BY score DESC NULLS LAST, id DESC"
    if limite:
        sql += f" LIMIT {int(limite)}"
    return [_annonce_depuis_row(r) for r in conn.execute(sql).fetchall()]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _bool_vers_int(valeur) -> int | None:
    if valeur is None:
        return None
    return 1 if valeur else 0


def _charger_json(texte, defaut):
    if not texte:
        return defaut
    try:
        return json.loads(texte)
    except (TypeError, ValueError):
        return defaut


def _analyse_depuis_row(row: sqlite3.Row) -> dict:
    return {
        "score_etat": row["score_etat"],
        "moisissure_detectee": bool(row["moisissure_detectee"]),
        "points_negatifs": _charger_json(row["points_negatifs"], []),
        "resume": row["resume_etat"],
        "nb_photos_analysees": row["nb_photos_analysees"],
        "analyse_le": row["photo_analyse_le"],
    }


def _annonce_depuis_row(row: sqlite3.Row) -> dict:
    annonce = {
        "id": row["id"],
        "source": row["source"],
        "titre": row["titre"],
        "ville": row["ville"],
        "prix": row["prix"],
        "surface": row["surface"],
        "pieces": row["pieces"],
        "url": row["url"],
        "score": row["score"],
        "photos": _charger_json(row["photos"], []),
        "analyse_photo": None,
    }
    if row["photo_analyse_le"] is not None:
        annonce["analyse_photo"] = _analyse_depuis_row(row)
    return annonce
