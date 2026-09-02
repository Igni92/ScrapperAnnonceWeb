"""Temps de trajet réels entre chaque annonce et les destinations configurées.

Services gratuits, sans clé (usage personnel et léger : voir les politiques dans config.py) :
  - géocodage : Base Adresse Nationale (BAN) sur la Géoplateforme (data.geopf.fr/geocodage)
  - voiture   : OSRM, serveur de démonstration public FOSSGIS (router.project-osrm.org)
  - transport : Transitous (api.transitous.org), routeur open data (GTFS Île-de-France Mobilités…)

Toutes les réponses sont mises en cache en base (tables `geocodage` et `trajets_cache`) : la
plupart des annonces d'une même ville partagent la même origine, donc très peu d'appels réseau.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

import config
import db

log = logging.getLogger(__name__)

MODES = {"transport": "🚇 transports", "voiture": "🚗 voiture"}


def _fuseau_paris():
    """Europe/Paris ; à défaut (Windows sans le paquet tzdata) le fuseau local de la machine."""
    try:
        return ZoneInfo("Europe/Paris")
    except ZoneInfoNotFoundError:
        log.warning("Fuseau Europe/Paris introuvable (installez le paquet tzdata) : fuseau local utilisé")
        return datetime.now().astimezone().tzinfo


PARIS = _fuseau_paris()
_session: requests.Session | None = None


def _http() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": config.TRAJET_USER_AGENT})
    return _session


def _get_json(url: str, params: dict | None = None):
    try:
        r = _http().get(url, params=params, timeout=config.TRAJET_TIMEOUT)
        r.raise_for_status()
        donnees = r.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Appel %s échoué : %s", url, exc)
        return None
    time.sleep(config.TRAJET_DELAI)
    return donnees


# ---------------------------------------------------------------------------
# Géocodage
# ---------------------------------------------------------------------------
def origine_annonce(annonce: dict) -> str:
    """Texte géocodé pour une annonce : adresse si connue, sinon ville + code postal."""
    morceaux = [annonce.get("adresse") or "", annonce.get("code_postal") or "", annonce.get("ville") or ""]
    return " ".join(m for m in morceaux if m).strip()


def geocoder(conn, requete: str, code_postal: str | None = None) -> dict | None:
    """Renvoie {"lat", "lon", "libelle"} ou None. Les résultats (même négatifs) sont mis en cache."""
    requete = " ".join((requete or "").split())
    if len(requete) < 3:
        return None
    cache = db.geocodage_cache_get(conn, requete)
    if cache is not None:
        return cache if cache.get("lat") is not None else None

    params = {"q": requete, "limit": 1}
    if code_postal:
        params["postcode"] = code_postal      # lève l'ambiguïté (Montreuil 93 vs Montreuil 85)
    donnees = _get_json(config.GEOCODAGE_URL, params)
    if donnees is None:
        return None                           # panne réseau : ne pas mettre en cache
    resultat = None
    features = donnees.get("features") or []
    if features:
        f = features[0]
        lon, lat = f["geometry"]["coordinates"]
        resultat = {"lat": lat, "lon": lon, "libelle": f.get("properties", {}).get("label", requete)}
    else:
        log.warning("Géocodage sans résultat pour « %s »", requete)
    db.geocodage_cache_set(conn, requete, resultat)
    return resultat


# ---------------------------------------------------------------------------
# Durées
# ---------------------------------------------------------------------------
def prochain_depart() -> datetime:
    """Prochain jour de semaine à TRAJET_HEURE_DEPART (heure de Paris), en UTC."""
    heure, minute = (int(x) for x in config.TRAJET_HEURE_DEPART.split(":"))
    maintenant = datetime.now(PARIS)
    depart = maintenant.replace(hour=heure, minute=minute, second=0, microsecond=0)
    if depart <= maintenant:
        depart += timedelta(days=1)
    while depart.weekday() >= 5:      # samedi / dimanche
        depart += timedelta(days=1)
    return depart.astimezone(timezone.utc)


def duree_voiture(o: dict, d: dict) -> tuple[float | None, dict]:
    url = f"{config.OSRM_URL}/route/v1/driving/{o['lon']},{o['lat']};{d['lon']},{d['lat']}"
    donnees = _get_json(url, {"overview": "false"})
    if not donnees or donnees.get("code") != "Ok" or not donnees.get("routes"):
        return None, {"erreur": (donnees or {}).get("message") or "pas d'itinéraire"}
    route = donnees["routes"][0]
    return route["duration"] / 60.0, {"distance_km": round(route.get("distance", 0) / 1000, 1)}


def duree_transport(o: dict, d: dict, depart: datetime | None = None) -> tuple[float | None, dict]:
    depart = depart or prochain_depart()
    params = {
        "fromPlace": f"{o['lat']},{o['lon']}",
        "toPlace": f"{d['lat']},{d['lon']}",
        "time": depart.strftime("%Y-%m-%dT%H:%M:%SZ"),   # RFC 3339 en UTC, obligatoire
        "arriveBy": "false",
        "numItineraries": 3,
        "maxTransfers": 4,
        "detailedTransfers": "false",
        "transitModes": "TRANSIT",
    }
    donnees = _get_json(f"{config.TRANSITOUS_URL}/api/v6/plan", params)
    itineraires = (donnees or {}).get("itineraries") or []
    if not itineraires:
        # Origine et destination très proches : pas de transport, seulement la marche (« direct »).
        directs = [d for d in (donnees or {}).get("direct") or [] if d.get("duration")]
        if directs:
            a_pied = min(directs, key=lambda d: d["duration"])
            return a_pied["duration"] / 60.0, {"correspondances": 0, "lignes": [], "a_pied": True}
        return None, {"erreur": "pas d'itinéraire en transports"}
    meilleur = min(itineraires, key=lambda i: i.get("duration", 10**9))
    return meilleur["duration"] / 60.0, {
        "correspondances": meilleur.get("transfers"),
        "lignes": [leg.get("routeShortName") for leg in meilleur.get("legs", []) if leg.get("routeShortName")],
    }


def duree(conn, origine: dict, origine_cle: str, destination: dict, mode: str) -> tuple[float | None, dict]:
    """Durée en minutes (ou None) entre deux points géocodés, avec cache."""
    dest_cle = destination["adresse"]
    heure = config.TRAJET_HEURE_DEPART if mode == "transport" else "-"
    cle = f"{origine_cle}|{dest_cle}|{mode}|{heure}"
    cache = db.trajet_cache_get(conn, cle)
    if cache is not None:
        return (int(cache["minutes"]) if cache["minutes"] is not None else None), cache["detail"]

    coord_dest = geocoder(conn, dest_cle)
    if coord_dest is None:
        return None, {"erreur": f"destination « {dest_cle} » introuvable"}
    if mode == "voiture":
        minutes, detail = duree_voiture(origine, coord_dest)
    else:
        minutes, detail = duree_transport(origine, coord_dest)
    if minutes is not None:
        minutes = round(minutes)
        db.trajet_cache_set(conn, cle, minutes, detail)   # les échecs ne sont pas mis en cache
    return minutes, detail


# ---------------------------------------------------------------------------
# Point d'entrée par annonce
# ---------------------------------------------------------------------------
def calculer_trajets(conn, annonce: dict, destinations: list[dict] | None = None) -> dict:
    """Remplit annonce["trajets"] et annonce["trajet_minutes"] ; renvoie le dict des trajets."""
    destinations = destinations if destinations is not None else config.DESTINATIONS
    annonce["trajets"], annonce["trajet_minutes"] = None, None
    if not destinations:
        return {}

    origine_cle = origine_annonce(annonce)
    origine = geocoder(conn, origine_cle, annonce.get("code_postal")) if origine_cle else None
    trajets: dict[str, dict] = {}
    for dest in destinations:
        mode = dest.get("mode", "transport")
        entree = {"mode": mode, "minutes": None, "max_minutes": dest.get("max_minutes"), "detail": {}}
        if origine is None:
            entree["detail"] = {"erreur": "annonce non géolocalisable"}
        else:
            entree["minutes"], entree["detail"] = duree(conn, origine, origine_cle, dest, mode)
        trajets[dest["nom"]] = entree

    connus = [int(t["minutes"]) for t in trajets.values() if t["minutes"] is not None]
    annonce["trajets"] = trajets
    # Durée retenue pour la notation : la pire des destinations connues.
    annonce["trajet_minutes"] = max(connus) if connus else None
    return trajets


def respecte_maximums(annonce: dict) -> bool:
    """False si une destination avec maximum est dépassée (ou inconnue et TRAJET_INCONNU_EXCLURE)."""
    for t in (annonce.get("trajets") or {}).values():
        maxi = t.get("max_minutes")
        if maxi is None:
            continue
        if t.get("minutes") is None:
            if config.TRAJET_INCONNU_EXCLURE:
                return False
            continue
        if t["minutes"] > maxi:
            return False
    return True


def resume_trajets(trajets: dict | None) -> str:
    """Texte court pour l'affichage : « Rungis 28 min 🚇 · Paris 40 min 🚗 »."""
    if not trajets:
        return ""
    parts = []
    for nom, t in trajets.items():
        icone = "🚇" if t.get("mode") == "transport" else "🚗"
        parts.append(f"{nom} {int(t['minutes'])} min {icone}" if t.get("minutes") is not None else f"{nom} ? {icone}")
    return " · ".join(parts)
