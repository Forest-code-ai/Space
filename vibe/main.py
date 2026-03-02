from __future__ import annotations

from pathlib import Path

import requests
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
STATIC_DIR = PROJECT_ROOT / "static"
INDEX_HTML = STATIC_DIR / "index.html"

app = FastAPI()

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def read_root() -> FileResponse:
    return FileResponse(path=str(INDEX_HTML), media_type="text/html")


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
