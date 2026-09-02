"""Communes pour la carte interactive : contours, centres, et temps de trajet par commune.

Sources gratuites, sans clé :
  - contours + centres : geo.api.gouv.fr (API officielle), en secours france-geojson (GitHub)
    et opendata.paris.fr pour les arrondissements de Paris.
Les contours sont mis en cache sur disque (dossier cache/), les trajets par commune en base.
"""

import json
import logging
import re
import time
from pathlib import Path

import requests

import config
import db
import trajets

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).with_name("cache")
GEO_API = "https://geo.api.gouv.fr/communes"
FRANCE_GEOJSON = ("https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/"
                  "departements/{dossier}/communes-{dossier}.geojson")
PARIS_ARRONDISSEMENTS = ("https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/"
                         "arrondissements/exports/geojson")
DEPARTEMENTS_IDF = {
    "75": "paris", "77": "seine-et-marne", "78": "yvelines", "91": "essonne",
    "92": "hauts-de-seine", "93": "seine-saint-denis", "94": "val-de-marne", "95": "val-d-oise",
}


def _get(url, params=None, timeout=60):
    try:
        r = requests.get(url, params=params, timeout=timeout,
                         headers={"User-Agent": config.TRAJET_USER_AGENT})
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Source communes indisponible %s : %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Géométrie
# ---------------------------------------------------------------------------
def centroide(geometry: dict) -> tuple[float, float] | None:
    """(lat, lon) du centre d'un Polygon / MultiPolygon GeoJSON (plus grand anneau extérieur)."""
    if not geometry:
        return None
    if geometry["type"] == "Polygon":
        anneaux = [geometry["coordinates"][0]]
    elif geometry["type"] == "MultiPolygon":
        anneaux = [p[0] for p in geometry["coordinates"]]
    else:
        return None
    meilleur, aire_max = None, -1.0
    for anneau in anneaux:
        aire, cx, cy = 0.0, 0.0, 0.0
        for (x1, y1), (x2, y2) in zip(anneau, anneau[1:] + anneau[:1]):
            croix = x1 * y2 - x2 * y1
            aire += croix
            cx += (x1 + x2) * croix
            cy += (y1 + y2) * croix
        if abs(aire) < 1e-12:
            continue
        aire /= 2.0
        if abs(aire) > aire_max:
            aire_max = abs(aire)
            meilleur = (cy / (6 * aire), cx / (6 * aire))   # (lat, lon)
    return meilleur


def nom_court(nom: str) -> str:
    """« Paris 12e Arrondissement » -> « Paris 12e » ; « 10ème Ardt » -> « Paris 10e »."""
    nom = re.sub(r"\s+Arrondissement$", "", nom.strip())
    m = re.match(r"^(\d+)(?:er|e|ème)\s+Ardt$", nom)
    if m:
        n = int(m.group(1))
        return f"Paris {n}{'er' if n == 1 else 'e'}"
    return nom


# ---------------------------------------------------------------------------
# Chargement des contours (avec cache disque)
# ---------------------------------------------------------------------------
def _depuis_geo_api(dep: str) -> list[dict] | None:
    params = {"codeDepartement": dep, "fields": "nom,code,codesPostaux,centre",
              "format": "geojson", "geometry": "contour"}
    if dep == "75":
        params["type"] = "arrondissement-municipal"
    donnees = _get(GEO_API, params)
    if not donnees or not donnees.get("features"):
        return None
    communes = []
    for f in donnees["features"]:
        p = f["properties"]
        centre = p.get("centre", {}).get("coordinates")
        latlon = (centre[1], centre[0]) if centre else centroide(f["geometry"])
        if latlon is None:
            continue
        communes.append({"code": p["code"], "nom": nom_court(p["nom"]), "dep": dep,
                         "codes_postaux": p.get("codesPostaux") or [],
                         "lat": round(latlon[0], 5), "lon": round(latlon[1], 5), "geometry": f["geometry"]})
    return communes


def _depuis_france_geojson(dep: str) -> list[dict] | None:
    if dep == "75":
        donnees = _get(PARIS_ARRONDISSEMENTS)
        if not donnees or not donnees.get("features"):
            return None
        communes = []
        for f in donnees["features"]:
            p = f["properties"]
            latlon = centroide(f["geometry"])
            if latlon is None:
                continue
            code = str(p.get("c_arinsee") or "")
            communes.append({"code": code, "nom": nom_court(p.get("l_ar", "")), "dep": dep,
                             "codes_postaux": [f"750{int(p['c_ar']):02d}"] if p.get("c_ar") else [],
                             "lat": round(latlon[0], 5), "lon": round(latlon[1], 5), "geometry": f["geometry"]})
        return sorted(communes, key=lambda c: c["code"])
    dossier = f"{dep}-{DEPARTEMENTS_IDF.get(dep, '')}"
    if dep not in DEPARTEMENTS_IDF:
        return None
    donnees = _get(FRANCE_GEOJSON.format(dossier=dossier))
    if not donnees or not donnees.get("features"):
        return None
    communes = []
    for f in donnees["features"]:
        p = f["properties"]
        latlon = centroide(f["geometry"])
        if latlon is None:
            continue
        communes.append({"code": p["code"], "nom": nom_court(p["nom"]), "dep": dep, "codes_postaux": [],
                         "lat": round(latlon[0], 5), "lon": round(latlon[1], 5), "geometry": f["geometry"]})
    return communes


def charger_departement(dep: str, forcer: bool = False) -> list[dict]:
    """Communes d'un département (liste de dicts avec geometry), depuis le cache disque ou le réseau."""
    dep = dep.strip().upper().zfill(2)
    CACHE_DIR.mkdir(exist_ok=True)
    fichier = CACHE_DIR / f"communes-{dep}.json"
    if fichier.exists() and not forcer:
        try:
            return json.loads(fichier.read_text(encoding="utf-8"))
        except ValueError:
            pass
    communes = _depuis_geo_api(dep) or _depuis_france_geojson(dep)
    if not communes:
        log.error("Impossible de charger les communes du département %s", dep)
        return []
    fichier.write_text(json.dumps(communes, ensure_ascii=False), encoding="utf-8")
    return communes


def charger_departements(deps: list[str] | None = None) -> list[dict]:
    resultat = []
    for dep in deps if deps is not None else config.CARTE_DEPARTEMENTS:
        resultat.extend(charger_departement(dep))
    return resultat


# ---------------------------------------------------------------------------
# Trajets par commune (suggestions)
# ---------------------------------------------------------------------------
def calculer_trajets_communes(conn, deps: list[str] | None = None, destinations: list[dict] | None = None,
                              progression=None) -> int:
    """Calcule (avec cache) le trajet de chaque commune des départements vers les destinations.

    Renvoie le nombre de communes traitées. `progression(i, total, commune)` est appelé à chaque commune.
    """
    destinations = destinations if destinations is not None else config.DESTINATIONS
    communes = charger_departements(deps)
    total = len(communes)
    for i, c in enumerate(communes, start=1):
        origine = {"lat": c["lat"], "lon": c["lon"]}
        origine_cle = f"commune:{c['code']}"
        resultat: dict[str, dict] = {}
        for dest in destinations:
            mode = dest.get("mode", "transport")
            minutes, detail = trajets.duree(conn, origine, origine_cle, dest, mode)
            resultat[dest["nom"]] = {"mode": mode, "minutes": minutes,
                                     "max_minutes": dest.get("max_minutes"), "detail": detail}
        connus = [t["minutes"] for t in resultat.values() if t["minutes"] is not None]
        pire = max(connus) if connus else None
        ok = trajets.respecte_maximums({"trajets": resultat}) if connus else None
        db.commune_trajets_set(conn, c["code"], c["nom"], c["dep"], c["lat"], c["lon"], resultat, pire, ok)
        if progression:
            progression(i, total, c)
    return total


def statuts(conn, deps: list[str] | None = None) -> dict[str, dict]:
    """{code: {"nom", "trajets", "trajet_minutes", "ok"}} pour les communes déjà calculées."""
    return db.communes_trajets_get(conn, deps if deps is not None else config.CARTE_DEPARTEMENTS)


def _normaliser(nom: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode().lower().strip()


def villes_suggerees(conn, deps: list[str] | None = None, exclure: list[str] | None = None) -> list[dict]:
    """Communes qui respectent tous les maximums, hors celles déjà dans `exclure`, triées par durée."""
    deja = {_normaliser(v) for v in (exclure if exclure is not None else config.VILLES)}
    suggestions = [c for c in statuts(conn, deps).values()
                   if c["ok"] and _normaliser(c["nom"]) not in deja]
    return sorted(suggestions, key=lambda c: (c["trajet_minutes"] is None, c["trajet_minutes"] or 0, c["nom"]))


def villes_pour_scraping() -> list[str]:
    """VILLES, complétées par les villes suggérées si SUGGESTIONS_AUTO est actif."""
    villes = list(config.VILLES)
    if not config.SUGGESTIONS_AUTO or not config.DESTINATIONS:
        return villes
    conn = db.init_db()
    try:
        for c in villes_suggerees(conn):
            villes.append(c["nom"])
    finally:
        conn.close()
    return villes
