"""Stockage SQLite des annonces + cache des analyses photo (clé : URL de l'annonce)."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
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
    "favori": "INTEGER NOT NULL DEFAULT 0",
    "masquee": "INTEGER NOT NULL DEFAULT 0",
    "notes": "TEXT",
    "code_postal": "TEXT",
    "adresse": "TEXT",
    "trajets": "TEXT",                 # JSON : {nom_destination: {"mode": ..., "minutes": ..., "max_minutes": ...}}
    "trajet_minutes": "REAL",          # durée retenue pour la notation (pire destination), NULL si inconnue
}

TRIS = {
    "score": "score DESC NULLS LAST, id DESC",
    "trajet": "trajet_minutes ASC NULLS LAST",
    "prix": "prix ASC NULLS LAST",
    "surface": "surface DESC NULLS LAST",
    "etat": "score_etat DESC NULLS LAST",
    "recent": "id DESC",
}


def connexion(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or config.DB_PATH, check_same_thread=False)
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
    # Caches réseau : géocodage et durées de trajet (évite de rappeler les API à chaque run).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS geocodage (
            requete   TEXT PRIMARY KEY,
            lat       REAL,
            lon       REAL,
            libelle   TEXT,
            calcule_le TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trajets_cache (
            cle        TEXT PRIMARY KEY,   -- origine|destination|mode|heure
            minutes    REAL,               -- NULL = calcul impossible
            detail     TEXT,               -- JSON libre (correspondances, distance…)
            calcule_le TEXT NOT NULL
        )
        """
    )
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
             nb_photos_analysees, photo_analyse_le, code_postal, adresse, trajets, trajet_minutes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            annonce.get("code_postal"),
            annonce.get("adresse"),
            json.dumps(annonce.get("trajets"), ensure_ascii=False) if annonce.get("trajets") else None,
            annonce.get("trajet_minutes"),
        ),
    )
    conn.commit()


def maj_trajets(conn: sqlite3.Connection, url: str, trajets: dict | None, trajet_minutes: float | None,
                score: float | None = None) -> None:
    conn.execute(
        "UPDATE annonces SET trajets = ?, trajet_minutes = ?, score = COALESCE(?, score) WHERE url = ?",
        (json.dumps(trajets, ensure_ascii=False) if trajets else None, trajet_minutes, score, url),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Caches réseau
# ---------------------------------------------------------------------------
def geocodage_cache_get(conn: sqlite3.Connection, requete: str) -> dict | None:
    row = conn.execute("SELECT lat, lon, libelle FROM geocodage WHERE requete = ?", (requete,)).fetchone()
    if row is None:
        return None
    return {"lat": row["lat"], "lon": row["lon"], "libelle": row["libelle"]}


def geocodage_cache_set(conn: sqlite3.Connection, requete: str, resultat: dict | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO geocodage (requete, lat, lon, libelle, calcule_le) VALUES (?, ?, ?, ?, ?)",
        (requete, (resultat or {}).get("lat"), (resultat or {}).get("lon"),
         (resultat or {}).get("libelle"), _maintenant()),
    )
    conn.commit()


def trajet_cache_get(conn: sqlite3.Connection, cle: str) -> dict | None:
    row = conn.execute("SELECT minutes, detail FROM trajets_cache WHERE cle = ?", (cle,)).fetchone()
    if row is None:
        return None
    return {"minutes": row["minutes"], "detail": _charger_json(row["detail"], {})}


def trajet_cache_set(conn: sqlite3.Connection, cle: str, minutes: float | None, detail: dict | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO trajets_cache (cle, minutes, detail, calcule_le) VALUES (?, ?, ?, ?)",
        (cle, minutes, json.dumps(detail or {}, ensure_ascii=False), _maintenant()),
    )
    conn.commit()


def vider_cache_trajets(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM trajets_cache")
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
        "SELECT * FROM annonces WHERE photo_analyse_le IS NULL AND masquee = 0 ORDER BY id"
    ).fetchall()
    return [_annonce_depuis_row(r) for r in rows]


def lister_annonces(conn: sqlite3.Connection, limite: int | None = None) -> list[dict]:
    sql = "SELECT * FROM annonces WHERE masquee = 0 ORDER BY score DESC NULLS LAST, id DESC"
    if limite:
        sql += f" LIMIT {int(limite)}"
    return [_annonce_depuis_row(r) for r in conn.execute(sql).fetchall()]


def toutes_annonces(conn: sqlite3.Connection) -> list[dict]:
    return [_annonce_depuis_row(r) for r in conn.execute("SELECT * FROM annonces ORDER BY id")]


# ---------------------------------------------------------------------------
# Interface web
# ---------------------------------------------------------------------------
def rechercher_annonces(conn: sqlite3.Connection, source: str | None = None, ville: str | None = None,
                        moisissure: bool | None = None, favoris: bool = False, masquees: bool = False,
                        q: str | None = None, score_min: float | None = None,
                        trajet_max: float | None = None,
                        tri: str = "score", limite: int | None = None) -> list[dict]:
    conditions, params = [], []
    conditions.append("masquee = 1" if masquees else "masquee = 0")
    if source:
        conditions.append("source = ?"); params.append(source)
    if ville:
        conditions.append("ville = ?"); params.append(ville)
    if moisissure is True:
        conditions.append("moisissure_detectee = 1")
    elif moisissure is False:
        conditions.append("(moisissure_detectee IS NULL OR moisissure_detectee = 0)")
    if favoris:
        conditions.append("favori = 1")
    if q:
        conditions.append("(titre LIKE ? OR ville LIKE ? OR resume_etat LIKE ?)")
        motif = f"%{q}%"
        params.extend([motif, motif, motif])
    if score_min is not None:
        conditions.append("score >= ?"); params.append(score_min)
    if trajet_max is not None:
        conditions.append("trajet_minutes IS NOT NULL AND trajet_minutes <= ?"); params.append(trajet_max)
    sql = "SELECT * FROM annonces WHERE " + " AND ".join(conditions)
    sql += " ORDER BY " + TRIS.get(tri, TRIS["score"])
    if limite:
        sql += f" LIMIT {int(limite)}"
    return [_annonce_depuis_row(r) for r in conn.execute(sql, params).fetchall()]


def get_annonce(conn: sqlite3.Connection, annonce_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM annonces WHERE id = ?", (annonce_id,)).fetchone()
    return _annonce_depuis_row(row) if row else None


def definir_drapeau(conn: sqlite3.Connection, annonce_id: int, champ: str, valeur: bool) -> None:
    if champ not in ("favori", "masquee"):
        raise ValueError(f"Champ inconnu : {champ}")
    conn.execute(f"UPDATE annonces SET {champ} = ? WHERE id = ?", (1 if valeur else 0, annonce_id))
    conn.commit()


def definir_notes(conn: sqlite3.Connection, annonce_id: int, notes: str) -> None:
    conn.execute("UPDATE annonces SET notes = ? WHERE id = ?", (notes.strip() or None, annonce_id))
    conn.commit()


def reinitialiser_analyse(conn: sqlite3.Connection, annonce_id: int) -> None:
    """Efface l'analyse photo : l'annonce sera reprise au prochain rattrapage."""
    conn.execute(
        """UPDATE annonces SET score_etat = NULL, moisissure_detectee = NULL, points_negatifs = NULL,
                  resume_etat = NULL, nb_photos_analysees = NULL, photo_analyse_le = NULL
            WHERE id = ?""",
        (annonce_id,),
    )
    conn.commit()


def supprimer_annonce(conn: sqlite3.Connection, annonce_id: int) -> None:
    conn.execute("DELETE FROM annonces WHERE id = ?", (annonce_id,))
    conn.commit()


def statistiques(conn: sqlite3.Connection) -> dict:
    depuis = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    row = conn.execute(
        """
        SELECT COUNT(*)                                            AS total,
               SUM(masquee = 0)                                    AS visibles,
               SUM(favori = 1)                                     AS favoris,
               SUM(moisissure_detectee = 1 AND masquee = 0)        AS moisissure,
               SUM(photo_analyse_le IS NULL AND masquee = 0)       AS non_analysees,
               AVG(CASE WHEN masquee = 0 THEN score END)           AS score_moyen,
               SUM(cree_le >= ?)                                   AS nouvelles_24h
          FROM annonces
        """,
        (depuis,),
    ).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}


def valeurs_distinctes(conn: sqlite3.Connection) -> dict:
    sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM annonces ORDER BY source")]
    villes = [r[0] for r in conn.execute(
        "SELECT DISTINCT ville FROM annonces WHERE ville IS NOT NULL AND ville != '' ORDER BY ville")]
    return {"sources": sources, "villes": villes}


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
    cles = row.keys()
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
        "cree_le": row["cree_le"],
        "photos": _charger_json(row["photos"], []),
        "favori": bool(row["favori"]) if "favori" in cles else False,
        "masquee": bool(row["masquee"]) if "masquee" in cles else False,
        "notes": row["notes"] if "notes" in cles else None,
        "code_postal": row["code_postal"] if "code_postal" in cles else None,
        "adresse": row["adresse"] if "adresse" in cles else None,
        "trajets": _charger_json(row["trajets"], None) if "trajets" in cles else None,
        "trajet_minutes": row["trajet_minutes"] if "trajet_minutes" in cles else None,
        "analyse_photo": None,
    }
    if row["photo_analyse_le"] is not None:
        annonce["analyse_photo"] = _analyse_depuis_row(row)
    return annonce
