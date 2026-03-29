from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sgp4.api import Satrec, jday

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
STATIC_DIR = PROJECT_ROOT / "static"
INDEX_HTML = STATIC_DIR / "index.html"

app = FastAPI()

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def read_root() -> FileResponse:
    return FileResponse(path=str(INDEX_HTML), media_type="text/html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/flights")
def get_flights() -> dict:
    """Fetch live flight state vectors for an Ontario-area bounding box via OpenSky."""
    url = "https://opensky-network.org/api/states/all"
    params = {
        "lamin": 43.5,
        "lomin": -80.0,
        "lamax": 45.5,
        "lomax": -76.0,
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"OpenSky request failed: {exc}") from exc

    payload = response.json()
    states = payload.get("states") or []

    flights: list[dict] = []
    for state in states:
        # OpenSky 'states' is a list of arrays. Indices per API docs.
        icao24 = state[0]
        callsign = (state[1] or "").strip()
        origin_country = state[2]
        time_position = state[3]
        last_contact = state[4]
        longitude = state[5]
        latitude = state[6]
        baro_altitude = state[7]
        on_ground = state[8]
        velocity = state[9]
        true_track = state[10]
        vertical_rate = state[11]
        geo_altitude = state[13]

        if longitude is None or latitude is None:
            continue

        flights.append(
            {
                "icao24": icao24,
                "callsign": callsign,
                "origin_country": origin_country,
                "time_position": time_position,
                "last_contact": last_contact,
                "lon": longitude,
                "lat": latitude,
                "baro_alt_m": baro_altitude,
                "geo_alt_m": geo_altitude,
                "on_ground": on_ground,
                "velocity_mps": velocity,
                "track_deg": true_track,
                "vertical_rate_mps": vertical_rate,
            }
        )

    return {
        "time": payload.get("time"),
        "count": len(flights),
        "bbox": params,
        "flights": flights,
    }


def _parse_tle(text: str) -> list[tuple[str, str, str]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    triples: list[tuple[str, str, str]] = []
    i = 0
    while i + 2 < len(lines):
        name = lines[i]
        l1 = lines[i + 1]
        l2 = lines[i + 2]
        if l1.startswith("1 ") and l2.startswith("2 "):
            triples.append((name, l1, l2))
            i += 3
        else:
            i += 1
    return triples


def _gmst_radians(jd_ut1: float) -> float:
    # Approx GMST from Vallado; sufficient for visualization.
    t = (jd_ut1 - 2451545.0) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd_ut1 - 2451545.0)
        + 0.000387933 * t * t
        - (t * t * t) / 38710000.0
    )
    gmst_deg = gmst_deg % 360.0
    return math.radians(gmst_deg)


def _teme_to_ecef(r_teme_m: tuple[float, float, float], jd_ut1: float) -> tuple[float, float, float]:
    # Ignore polar motion and equation-of-equinoxes; z-rotation by GMST.
    x, y, z = r_teme_m
    theta = _gmst_radians(jd_ut1)
    c = math.cos(theta)
    s = math.sin(theta)
    x_ecef = c * x + s * y
    y_ecef = -s * x + c * y
    return (x_ecef, y_ecef, z)


def _ecef_to_geodetic_wgs84(r_ecef_m: tuple[float, float, float]) -> tuple[float, float, float]:
    # Convert ECEF (m) to geodetic lat/lon (deg) and altitude (m) on WGS84.
    x, y, z = r_ecef_m
    a = 6378137.0
    f = 1.0 / 298.257223563
    e2 = f * (2.0 - f)

    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    if p < 1e-9:
        lat = math.copysign(math.pi / 2.0, z)
        alt = abs(z) - a * (1.0 - f)
        return (math.degrees(lat), math.degrees(lon), alt)

    def _compute_alt(p: float, z: float, lat: float, n: float) -> float:
        cos_lat = math.cos(lat)
        # Near the poles cos(lat) → 0; use the z-axis formula instead.
        if abs(cos_lat) > 1e-9:
            return p / cos_lat - n
        return abs(z) / abs(math.sin(lat)) - n * (1.0 - e2)

    lat = math.atan2(z, p * (1.0 - e2))
    for _ in range(6):
        sin_lat = math.sin(lat)
        n = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
        alt = _compute_alt(p, z, lat, n)
        lat_next = math.atan2(z, p * (1.0 - e2 * (n / (n + alt))))
        if abs(lat_next - lat) < 1e-12:
            lat = lat_next
            break
        lat = lat_next

    sin_lat = math.sin(lat)
    n = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
    alt = _compute_alt(p, z, lat, n)
    return (math.degrees(lat), math.degrees(lon), alt)


@app.get("/api/satellites")
def get_satellites() -> list[dict]:
    url = "https://celestrak.org/NORAD/elements/gp.php"
    params = {"GROUP": "visual", "FORMAT": "tle"}

    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"CelesTrak request failed: {exc}") from exc

    triples = _parse_tle(response.text)

    now = datetime.now(timezone.utc)
    jd, fr = jday(
        now.year,
        now.month,
        now.day,
        now.hour,
        now.minute,
        now.second + now.microsecond / 1_000_000.0,
    )
    jd_ut1 = jd + fr

    sats: list[dict] = []
    for name, l1, l2 in triples:
        try:
            sat = Satrec.twoline2rv(l1, l2)
            err, r_km, _v_km_s = sat.sgp4(jd, fr)
        except Exception:
            continue

        if err != 0:
            continue

        r_teme_m = (r_km[0] * 1000.0, r_km[1] * 1000.0, r_km[2] * 1000.0)
        r_ecef_m = _teme_to_ecef(r_teme_m, jd_ut1)
        lat, lon, alt_m = _ecef_to_geodetic_wgs84(r_ecef_m)

        sats.append(
            {
                "name": name,
                "lat": lat,
                "lon": lon,
                "alt": alt_m,
            }
        )

    return sats
