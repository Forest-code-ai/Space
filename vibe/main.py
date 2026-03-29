from __future__ import annotations

import logging
import math
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sgp4.api import Satrec, jday

load_dotenv()

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
STATIC_DIR = PROJECT_ROOT / "static"
INDEX_HTML = STATIC_DIR / "index.html"

# ── Configurable bounding box (Ontario defaults) ───────────────────────────
FLIGHTS_LAMIN = float(os.getenv("FLIGHTS_LAMIN", "43.5"))
FLIGHTS_LOMIN = float(os.getenv("FLIGHTS_LOMIN", "-80.0"))
FLIGHTS_LAMAX = float(os.getenv("FLIGHTS_LAMAX", "45.5"))
FLIGHTS_LOMAX = float(os.getenv("FLIGHTS_LOMAX", "-76.0"))

# Optional AIS stream API key for live vessel data
AISSTREAM_API_KEY = os.getenv("AISSTREAM_API_KEY", "")

log.info(
    "Space config — bbox: (%.2f,%.2f)→(%.2f,%.2f)  AIS key: %s",
    FLIGHTS_LAMIN, FLIGHTS_LOMIN, FLIGHTS_LAMAX, FLIGHTS_LOMAX,
    "SET" if AISSTREAM_API_KEY else "unset (demo mode)",
)

# ── In-memory TTL cache ────────────────────────────────────────────────────

class _Cache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, ttl: float) -> Any:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > ttl:
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic(), value)


_cache = _Cache()

# ── Simple sliding-window rate limiter (30 req/min per IP) ────────────────

class _RateLimiter:
    def __init__(self, limit: int = 30, window: float = 60.0) -> None:
        self._limit = limit
        self._window = window
        self._buckets: dict[str, deque[float]] = {}

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        dq = self._buckets.setdefault(key, deque())
        while dq and now - dq[0] > self._window:
            dq.popleft()
        if len(dq) >= self._limit:
            return False
        dq.append(now)
        return True

    def retry_after(self, key: str) -> int:
        dq = self._buckets.get(key)
        if not dq:
            return 1
        now = time.monotonic()
        return max(1, int(self._window - (now - dq[0])) + 1)


_limiter = _RateLimiter(limit=30, window=60.0)

# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def security_and_rate_limit(request: Request, call_next: Any) -> Response:
    if request.url.path.startswith("/api/"):
        client_ip = request.client.host if request.client else "unknown"
        if not _limiter.is_allowed(client_ip):
            retry = _limiter.retry_after(client_ip)
            log.warning("Rate limit hit from %s — retry in %ds", client_ip, retry)
            return JSONResponse(
                status_code=429,
                content={"error": "Rate limit exceeded", "retry_after_seconds": retry},
                headers={"Retry-After": str(retry)},
            )

    response: Response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cesium.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cesium.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https://*.openstreetmap.org https://*.tile.openstreetmap.org https://*.basemaps.cartocdn.com; "
        "connect-src 'self'; "
        "worker-src blob:;"
    )
    return response


@app.get("/", include_in_schema=False)
def read_root() -> FileResponse:
    return FileResponse(path=str(INDEX_HTML), media_type="text/html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


# ── Flights ────────────────────────────────────────────────────────────────

def _safe_float(val: Any) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _parse_state(state: list) -> dict | None:
    if len(state) < 17:
        return None
    longitude = _safe_float(state[5])
    latitude = _safe_float(state[6])
    if longitude is None or latitude is None:
        return None
    return {
        "icao24": str(state[0] or ""),
        "callsign": str(state[1] or "").strip(),
        "origin_country": str(state[2] or ""),
        "time_position": _safe_float(state[3]),
        "last_contact": _safe_float(state[4]),
        "lon": longitude,
        "lat": latitude,
        "baro_alt_m": _safe_float(state[7]),
        "geo_alt_m": _safe_float(state[13]),
        "on_ground": bool(state[8]),
        "velocity_mps": _safe_float(state[9]),
        "track_deg": _safe_float(state[10]),
        "vertical_rate_mps": _safe_float(state[11]),
    }


@app.get("/api/flights")
def get_flights() -> dict:
    """Fetch live flight state vectors for a configurable bounding box via OpenSky."""
    cached = _cache.get("flights", ttl=9.0)
    if cached is not None:
        return cached

    bbox = {
        "lamin": FLIGHTS_LAMIN,
        "lomin": FLIGHTS_LOMIN,
        "lamax": FLIGHTS_LAMAX,
        "lomax": FLIGHTS_LOMAX,
    }

    try:
        response = requests.get(
            "https://opensky-network.org/api/states/all",
            params=bbox,
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("OpenSky request failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "OpenSky request failed", "source": str(exc)},
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        log.warning("OpenSky returned invalid JSON: %s", exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "OpenSky returned invalid JSON", "source": str(exc)},
        ) from exc

    states = payload.get("states") or []
    flights: list[dict] = []
    for state in states:
        if not isinstance(state, list):
            continue
        parsed = _parse_state(state)
        if parsed is not None:
            flights.append(parsed)

    result = {
        "time": payload.get("time"),
        "count": len(flights),
        "bbox": bbox,
        "flights": flights,
    }
    _cache.set("flights", result)
    return result


# ── Satellite orbital mechanics helpers ───────────────────────────────────

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
    t = (jd_ut1 - 2451545.0) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd_ut1 - 2451545.0)
        + 0.000387933 * t * t
        - (t * t * t) / 38710000.0
    )
    return math.radians(gmst_deg % 360.0)


def _teme_to_ecef(
    r_teme_m: tuple[float, float, float], jd_ut1: float
) -> tuple[float, float, float]:
    x, y, z = r_teme_m
    theta = _gmst_radians(jd_ut1)
    c, s = math.cos(theta), math.sin(theta)
    return (c * x + s * y, -s * x + c * y, z)


def _ecef_to_geodetic_wgs84(
    r_ecef_m: tuple[float, float, float],
) -> tuple[float, float, float]:
    x, y, z = r_ecef_m
    a = 6378137.0
    f = 1.0 / 298.257223563
    e2 = f * (2.0 - f)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    if p < 1e-9:
        lat = math.copysign(math.pi / 2.0, z)
        return (math.degrees(lat), math.degrees(lon), abs(z) - a * (1.0 - f))

    def _alt(p: float, z: float, lat: float, n: float) -> float:
        cl = math.cos(lat)
        if abs(cl) > 1e-9:
            return p / cl - n
        return abs(z) / abs(math.sin(lat)) - n * (1.0 - e2)

    lat = math.atan2(z, p * (1.0 - e2))
    for _ in range(6):
        sl = math.sin(lat)
        n = a / math.sqrt(1.0 - e2 * sl * sl)
        alt = _alt(p, z, lat, n)
        lat_next = math.atan2(z, p * (1.0 - e2 * (n / (n + alt))))
        if abs(lat_next - lat) < 1e-12:
            lat = lat_next
            break
        lat = lat_next

    sl = math.sin(lat)
    n = a / math.sqrt(1.0 - e2 * sl * sl)
    return (math.degrees(lat), math.degrees(lon), _alt(p, z, lat, n))


def _mean_motion_to_period_min(mean_motion_revs_per_day: float) -> float | None:
    if mean_motion_revs_per_day <= 0:
        return None
    return round(1440.0 / mean_motion_revs_per_day, 1)


@app.get("/api/satellites")
def get_satellites() -> list[dict]:
    cached = _cache.get("satellites", ttl=14.0)
    if cached is not None:
        return cached

    try:
        response = requests.get(
            "https://celestrak.org/NORAD/elements/gp.php",
            params={"GROUP": "visual", "FORMAT": "tle"},
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("CelesTrak request failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "CelesTrak request failed", "source": str(exc)},
        ) from exc

    triples = _parse_tle(response.text)

    now = datetime.now(timezone.utc)
    jd, fr = jday(
        now.year, now.month, now.day,
        now.hour, now.minute,
        now.second + now.microsecond / 1_000_000.0,
    )
    jd_ut1 = jd + fr

    sats: list[dict] = []
    for name, l1, l2 in triples:
        try:
            sat = Satrec.twoline2rv(l1, l2)
            err, r_km, _v = sat.sgp4(jd, fr)
        except Exception:
            continue
        if err != 0:
            continue

        r_teme_m = (r_km[0] * 1000.0, r_km[1] * 1000.0, r_km[2] * 1000.0)
        r_ecef_m = _teme_to_ecef(r_teme_m, jd_ut1)
        lat, lon, alt_m = _ecef_to_geodetic_wgs84(r_ecef_m)

        sats.append({
            "name": name,
            "lat": lat,
            "lon": lon,
            "alt": alt_m,
            "period_min": _mean_motion_to_period_min(
                sat.no_kozai * 60.0 * 24.0 / (2 * math.pi)
            ),
        })

    _cache.set("satellites", sats)
    return sats


# ── Supply chain: vessel tracking ─────────────────────────────────────────

_DEMO_VESSEL_ROUTES: list[dict] = [
    # North Atlantic (eastbound)
    {"mmsi": "123456001", "name": "MV ATLANTIC SPIRIT",   "base_lat":  46.5,  "base_lon":  -38.2, "heading_deg":  72, "speed_kn": 14.2, "vessel_type": "CARGO",  "flag": "PA", "dest_port": "ROTTERDAM"},
    {"mmsi": "123456002", "name": "MV NORDIC CARRIER",    "base_lat":  48.1,  "base_lon":  -22.7, "heading_deg":  68, "speed_kn": 13.8, "vessel_type": "CARGO",  "flag": "BS", "dest_port": "HAMBURG"},
    {"mmsi": "123456003", "name": "MV OCEAN PIONEER",     "base_lat":  44.3,  "base_lon":  -54.1, "heading_deg":  75, "speed_kn": 15.1, "vessel_type": "TANKER", "flag": "MH", "dest_port": "ROTTERDAM"},
    # North Atlantic (westbound)
    {"mmsi": "123456004", "name": "MV WESTWARD HO",       "base_lat":  41.2,  "base_lon":  -28.4, "heading_deg": 268, "speed_kn": 13.0, "vessel_type": "CARGO",  "flag": "LR", "dest_port": "NEW YORK"},
    # English Channel / North Sea
    {"mmsi": "123456005", "name": "MV CHANNEL TRADER",    "base_lat":  51.3,  "base_lon":    1.6, "heading_deg":  55, "speed_kn": 10.2, "vessel_type": "CARGO",  "flag": "NL", "dest_port": "ANTWERP"},
    {"mmsi": "123456006", "name": "MV DOVER STRAIT",      "base_lat":  51.0,  "base_lon":    2.1, "heading_deg": 310, "speed_kn":  9.8, "vessel_type": "TANKER", "flag": "GB", "dest_port": "FELIXSTOWE"},
    # Suez corridor
    {"mmsi": "123456007", "name": "MV SUEZ PASSAGE",      "base_lat":  28.5,  "base_lon":   33.1, "heading_deg": 160, "speed_kn": 11.5, "vessel_type": "CARGO",  "flag": "CY", "dest_port": "SINGAPORE"},
    {"mmsi": "123456008", "name": "MV RED SEA STAR",       "base_lat":  22.4,  "base_lon":   38.6, "heading_deg": 150, "speed_kn": 12.0, "vessel_type": "TANKER", "flag": "PA", "dest_port": "MUMBAI (JNPT)"},
    # Indian Ocean
    {"mmsi": "123456009", "name": "MV INDIAN VOYAGER",    "base_lat":   6.2,  "base_lon":   68.3, "heading_deg":  95, "speed_kn": 13.5, "vessel_type": "CARGO",  "flag": "IN", "dest_port": "SINGAPORE"},
    # Strait of Malacca
    {"mmsi": "123456010", "name": "MV STRAITS BOUND",     "base_lat":   3.4,  "base_lon":  101.2, "heading_deg": 140, "speed_kn": 10.8, "vessel_type": "TANKER", "flag": "SG", "dest_port": "SINGAPORE"},
    # Trans-Pacific (eastbound)
    {"mmsi": "123456011", "name": "MV PACIFIC BRIDGE",    "base_lat":  35.8,  "base_lon":  172.4, "heading_deg":  85, "speed_kn": 16.0, "vessel_type": "CARGO",  "flag": "PA", "dest_port": "LOS ANGELES"},
    {"mmsi": "123456012", "name": "MV TRANS-PAC GLORY",   "base_lat":  28.1,  "base_lon":  155.6, "heading_deg":  78, "speed_kn": 15.4, "vessel_type": "CARGO",  "flag": "MH", "dest_port": "LOS ANGELES"},
    # Trans-Pacific (westbound)
    {"mmsi": "123456013", "name": "MV WESTPAC TRADER",    "base_lat":  38.3,  "base_lon": -148.2, "heading_deg": 272, "speed_kn": 14.7, "vessel_type": "CARGO",  "flag": "HK", "dest_port": "SHANGHAI"},
    # East Asia
    {"mmsi": "123456014", "name": "MV EAST CHINA SEA",    "base_lat":  29.8,  "base_lon":  122.4, "heading_deg":  35, "speed_kn":  9.2, "vessel_type": "CARGO",  "flag": "CN", "dest_port": "SHANGHAI"},
    {"mmsi": "123456015", "name": "MV KOREA STAR",        "base_lat":  34.6,  "base_lon":  128.8, "heading_deg":  20, "speed_kn": 11.0, "vessel_type": "TANKER", "flag": "KR", "dest_port": "BUSAN"},
    # South America
    {"mmsi": "123456016", "name": "MV AMAZON TRADER",     "base_lat": -22.8,  "base_lon":  -41.4, "heading_deg":  95, "speed_kn": 12.8, "vessel_type": "CARGO",  "flag": "BR", "dest_port": "SANTOS"},
    # Africa
    {"mmsi": "123456017", "name": "MV CAPE HOPE",         "base_lat": -33.4,  "base_lon":   17.8, "heading_deg": 210, "speed_kn": 13.2, "vessel_type": "TANKER", "flag": "ZA", "dest_port": "DURBAN"},
    # Mediterranean
    {"mmsi": "123456018", "name": "MV MEDITERRAN",        "base_lat":  36.2,  "base_lon":   14.5, "heading_deg": 110, "speed_kn":  9.5, "vessel_type": "CARGO",  "flag": "GR", "dest_port": "PIRAEUS"},
    # Gulf of Mexico
    {"mmsi": "123456019", "name": "MV GULF RUNNER",       "base_lat":  24.3,  "base_lon":  -89.7, "heading_deg":  30, "speed_kn": 11.8, "vessel_type": "TANKER", "flag": "US", "dest_port": "NEW YORK"},
    # Australia
    {"mmsi": "123456020", "name": "MV SOUTHERN CROSS",    "base_lat": -31.5,  "base_lon":  114.8, "heading_deg": 170, "speed_kn": 10.5, "vessel_type": "CARGO",  "flag": "AU", "dest_port": "SYDNEY"},
]

_KM_PER_DEG_LAT = 111.32
# Vessels cycle their route every 10 days, keeping positions near their base.
_VESSEL_CYCLE_S = 10 * 24 * 3600.0


def _drift_position(v: dict, now_s: float) -> tuple[float, float]:
    """Compute current vessel position using cyclic drift around base coordinates."""
    speed_ms = v["speed_kn"] * 0.514444
    hdg_rad = math.radians(v["heading_deg"])
    # Per-vessel phase offset derived from MMSI so ships stagger across the cycle.
    phase_offset = (int(v["mmsi"][-3:]) / 1000.0) * _VESSEL_CYCLE_S
    phase = ((now_s + phase_offset) % _VESSEL_CYCLE_S) / _VESSEL_CYCLE_S
    direction = phase if phase < 0.5 else (1.0 - phase)
    dist_m = speed_ms * (direction * _VESSEL_CYCLE_S)
    dlat = (dist_m * math.cos(hdg_rad)) / (1000.0 * _KM_PER_DEG_LAT)
    cos_lat = math.cos(math.radians(v["base_lat"]))
    dlon = (dist_m * math.sin(hdg_rad)) / (1000.0 * _KM_PER_DEG_LAT * (cos_lat or 1e-9))
    lat = v["base_lat"] + dlat
    lon = ((v["base_lon"] + dlon + 180.0) % 360.0) - 180.0
    # Clamp lat to valid range.
    lat = max(-85.0, min(85.0, lat))
    return lat, lon


def _build_demo_vessels(now_s: float) -> list[dict]:
    return [
        {
            "mmsi": v["mmsi"],
            "name": v["name"],
            "lat": round(_drift_position(v, now_s)[0], 5),
            "lon": round(_drift_position(v, now_s)[1], 5),
            "heading_deg": v["heading_deg"],
            "speed_kn": v["speed_kn"],
            "vessel_type": v["vessel_type"],
            "flag": v["flag"],
            "dest_port": v["dest_port"],
            "demo": True,
        }
        for v in _DEMO_VESSEL_ROUTES
    ]


def _fetch_live_vessels() -> list[dict]:
    """Attempt to fetch vessel positions from aisstream.io REST API."""
    url = "https://api.aisstream.io/v0/vessels"
    headers = {"Authorization": f"Bearer {AISSTREAM_API_KEY}"}
    vessels: list[dict] = []
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if not resp.ok:
            return []
        data = resp.json()
        for item in data if isinstance(data, list) else []:
            try:
                vessels.append({
                    "mmsi": str(item.get("mmsi", "")),
                    "name": str(item.get("name", "UNKNOWN")).strip(),
                    "lat": float(item["latitude"]),
                    "lon": float(item["longitude"]),
                    "heading_deg": float(item.get("heading") or 0),
                    "speed_kn": float(item.get("speed") or 0),
                    "vessel_type": str(item.get("shipType", "CARGO")),
                    "flag": str(item.get("flag", "")),
                    "dest_port": str(item.get("destination", "")).strip(),
                    "demo": False,
                })
            except (KeyError, TypeError, ValueError):
                continue
    except requests.RequestException:
        pass
    return vessels


@app.get("/api/vessels")
def get_vessels() -> list[dict]:
    """Return vessel positions — live (if API key configured) or demo data."""
    cached = _cache.get("vessels", ttl=30.0)
    if cached is not None:
        return cached

    now_s = time.time()
    vessels = _fetch_live_vessels() if AISSTREAM_API_KEY else []
    if not vessels:
        vessels = _build_demo_vessels(now_s)

    _cache.set("vessels", vessels)
    return vessels


# ── Supply chain: world ports ──────────────────────────────────────────────

_WORLD_PORTS: list[dict] = [
    # Europe
    {"code": "NLRTM", "name": "Rotterdam",        "lat": 51.9225, "lon":   4.4792, "type": "MEGA_HUB",    "country": "NL"},
    {"code": "BEANR", "name": "Antwerp",           "lat": 51.2994, "lon":   4.3432, "type": "MEGA_HUB",    "country": "BE"},
    {"code": "DEHAM", "name": "Hamburg",           "lat": 53.5503, "lon":   9.9920, "type": "MAJOR_HUB",   "country": "DE"},
    {"code": "GBFXT", "name": "Felixstowe",        "lat": 51.9614, "lon":   1.3513, "type": "MAJOR_HUB",   "country": "GB"},
    {"code": "GBSOU", "name": "Southampton",       "lat": 50.9025, "lon":  -1.4042, "type": "MAJOR_HUB",   "country": "GB"},
    {"code": "ESBCN", "name": "Barcelona",         "lat": 41.3851, "lon":   2.1734, "type": "REGIONAL_HUB","country": "ES"},
    {"code": "ITGOA", "name": "Genoa",             "lat": 44.4056, "lon":   8.9463, "type": "REGIONAL_HUB","country": "IT"},
    {"code": "GRPIR", "name": "Piraeus",           "lat": 37.9480, "lon":  23.6443, "type": "MAJOR_HUB",   "country": "GR"},
    {"code": "TRMER", "name": "Mersin",            "lat": 36.8121, "lon":  34.6415, "type": "REGIONAL_HUB","country": "TR"},
    # North America
    {"code": "USLAX", "name": "Los Angeles",       "lat": 33.7300, "lon": -118.2700,"type": "MEGA_HUB",    "country": "US"},
    {"code": "USNYC", "name": "New York",          "lat": 40.6643, "lon":  -74.0100,"type": "MEGA_HUB",    "country": "US"},
    {"code": "USSAV", "name": "Savannah",          "lat": 32.0809, "lon":  -81.0912,"type": "MAJOR_HUB",   "country": "US"},
    {"code": "USHOU", "name": "Houston",           "lat": 29.7604, "lon":  -95.3698,"type": "MAJOR_HUB",   "country": "US"},
    {"code": "CAVAN", "name": "Vancouver",         "lat": 49.2827, "lon": -123.1207,"type": "MAJOR_HUB",   "country": "CA"},
    {"code": "CAMTR", "name": "Montreal",          "lat": 45.5017, "lon":  -73.5673,"type": "REGIONAL_HUB","country": "CA"},
    # South America
    {"code": "BRSSZ", "name": "Santos",            "lat": -23.9608,"lon":  -46.3221,"type": "MAJOR_HUB",   "country": "BR"},
    {"code": "BRRIO", "name": "Rio de Janeiro",    "lat": -22.9068,"lon":  -43.1729,"type": "REGIONAL_HUB","country": "BR"},
    {"code": "CLVAP", "name": "Valparaíso",        "lat": -33.0458,"lon":  -71.6197,"type": "REGIONAL_HUB","country": "CL"},
    # Asia
    {"code": "CNSHA", "name": "Shanghai",          "lat": 31.2304, "lon": 121.4737, "type": "MEGA_HUB",    "country": "CN"},
    {"code": "CNNGB", "name": "Ningbo",            "lat": 29.8683, "lon": 121.5440, "type": "MEGA_HUB",    "country": "CN"},
    {"code": "CNSZX", "name": "Shenzhen",          "lat": 22.5431, "lon": 114.0579, "type": "MEGA_HUB",    "country": "CN"},
    {"code": "CNTJN", "name": "Tianjin",           "lat": 39.3434, "lon": 117.3616, "type": "MAJOR_HUB",   "country": "CN"},
    {"code": "HKHKG", "name": "Hong Kong",         "lat": 22.3193, "lon": 114.1694, "type": "MEGA_HUB",    "country": "HK"},
    {"code": "SGSIN", "name": "Singapore",         "lat":  1.2897, "lon": 103.8501, "type": "MEGA_HUB",    "country": "SG"},
    {"code": "KRPUS", "name": "Busan",             "lat": 35.1796, "lon": 129.0756, "type": "MEGA_HUB",    "country": "KR"},
    {"code": "JPYOK", "name": "Yokohama",          "lat": 35.4437, "lon": 139.6380, "type": "MAJOR_HUB",   "country": "JP"},
    {"code": "MYTPP", "name": "Port Klang",        "lat":  3.0006, "lon": 101.3901, "type": "MAJOR_HUB",   "country": "MY"},
    {"code": "LKCMB", "name": "Colombo",           "lat":  6.9271, "lon":  79.8612, "type": "MAJOR_HUB",   "country": "LK"},
    {"code": "INNSA", "name": "Mumbai (JNPT)",     "lat": 18.9548, "lon":  72.9319, "type": "MAJOR_HUB",   "country": "IN"},
    {"code": "INMAA", "name": "Chennai",           "lat": 13.0827, "lon":  80.2707, "type": "REGIONAL_HUB","country": "IN"},
    {"code": "TWKHH", "name": "Kaohsiung",         "lat": 22.6273, "lon": 120.3014, "type": "MAJOR_HUB",   "country": "TW"},
    {"code": "VNSGN", "name": "Ho Chi Minh City",  "lat": 10.7769, "lon": 106.7009, "type": "REGIONAL_HUB","country": "VN"},
    # Middle East
    {"code": "AEJEA", "name": "Jebel Ali",         "lat": 24.9857, "lon":  55.0878, "type": "MEGA_HUB",    "country": "AE"},
    {"code": "OMMCT", "name": "Muscat",            "lat": 23.6139, "lon":  58.5922, "type": "REGIONAL_HUB","country": "OM"},
    # Africa
    {"code": "ZADUR", "name": "Durban",            "lat": -29.8587,"lon":  31.0218, "type": "MAJOR_HUB",   "country": "ZA"},
    {"code": "EGPSD", "name": "Port Said",         "lat": 31.2653, "lon":  32.3019, "type": "MAJOR_HUB",   "country": "EG"},
    {"code": "MAPTM", "name": "Tanger Med",        "lat": 35.8850, "lon":  -5.5008, "type": "MAJOR_HUB",   "country": "MA"},
    {"code": "NGAPP", "name": "Apapa (Lagos)",     "lat":  6.4427, "lon":   3.3810, "type": "REGIONAL_HUB","country": "NG"},
    # Oceania
    {"code": "AUSYD", "name": "Sydney",            "lat": -33.8688,"lon": 151.2093, "type": "MAJOR_HUB",   "country": "AU"},
    {"code": "AUMEL", "name": "Melbourne",         "lat": -37.8136,"lon": 144.9631, "type": "MAJOR_HUB",   "country": "AU"},
    {"code": "NZAKL", "name": "Auckland",          "lat": -36.8485,"lon": 174.7633, "type": "REGIONAL_HUB","country": "NZ"},
]


@app.get("/api/ports")
def get_ports() -> list[dict]:
    """Return the static world-port dataset."""
    return _WORLD_PORTS


# ── Supply chain: proximity status ────────────────────────────────────────

_APPROACH_THRESHOLD_KM = 200.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


@app.get("/api/supply-chain/status")
def get_supply_chain_status() -> dict:
    """Compute vessel-to-port proximity events."""
    cached = _cache.get("sc_status", ttl=30.0)
    if cached is not None:
        return cached

    vessels_cached = _cache.get("vessels", ttl=30.0)
    vessels = vessels_cached if vessels_cached is not None else _build_demo_vessels(time.time())

    events: list[dict] = []
    for v in vessels:
        closest_port = None
        closest_dist = float("inf")
        for p in _WORLD_PORTS:
            d = _haversine_km(v["lat"], v["lon"], p["lat"], p["lon"])
            if d < closest_dist:
                closest_dist = d
                closest_port = p
        if closest_port and closest_dist <= _APPROACH_THRESHOLD_KM:
            status = "AT PORT" if closest_dist < 20.0 else "APPROACHING"
            events.append({
                "mmsi": v["mmsi"],
                "vessel": v["name"],
                "port": closest_port["name"],
                "port_code": closest_port["code"],
                "dist_km": round(closest_dist, 1),
                "status": status,
                "vessel_type": v["vessel_type"],
            })

    result = {
        "vessel_count": len(vessels),
        "port_count": len(_WORLD_PORTS),
        "approaching_count": len(events),
        "events": sorted(events, key=lambda e: e["dist_km"]),
    }
    _cache.set("sc_status", result)
    return result
