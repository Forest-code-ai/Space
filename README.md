# SpatialOS: WorldView Prototype

A browser-based spatial intelligence and surveillance simulator.

This prototype explores the concept of “sousveillance” and spatial data fusion by layering real-time, open-source intelligence (OSINT) feeds over a 3D navigable globe. By combining live telemetry with military-grade WebGL shaders, it transforms public data into an intelligence dashboard.

## 👁️ The Vision

We are moving beyond language models into **spatial intelligence**—systems that understand physical relationships, continuous movement, and change over time. This project is a foundational data engine that ingests raw telemetry (flights, satellites, cameras) and makes the physical world queryable in real-time.

## 🚀 Features

* **Real-Time Data Fusion Engine:** A FastAPI backend that acts as a secure proxy, ingesting and parsing complex public data streams to avoid frontend CORS issues and rate limits.
* **Live Orbital Mechanics:** Pulls raw Two-Line Element (TLE) sets from CelesTrak and calculates live satellite coordinates using the `sgp4` physical modeling library.
* **ADS-B Flight Tracking:** Real-time aircraft positioning and callsign tracking using OpenSky Network telemetry.
* **Tactical WebGL Shaders:** Custom `Cesium.PostProcessStage` fragment shaders that hijack the rendering pipeline to compute screen-space luminance, mapping it to:
  * **Night Vision:** High-contrast phosphor green with CRT scanlines and animated film grain.
  * **FLIR Thermal:** Heat-signature simulation utilizing a localized color ramp and high-luminance blooming.
* **Spatial Video Projection:** Drapes live looping video feeds onto specific geographical coordinate geometry (Test Zone: Hunter St & Bethune St intersection).
* **“God Mode” HUD:** A responsive, absolute-positioned tactical overlay featuring live mouse coordinate targeting and sensor array toggles.

## 🛠️ Tech Stack

**Backend (Data Engine):**
* Python 3
* FastAPI & Uvicorn (Lightweight asynchronous web server)
* `sgp4` (Earth satellite orbital tracking)
* `requests` (API ingestion)

**Frontend (Render Engine):**
* CesiumJS (3D Geospatial Globe)
* WebGL (Custom Fragment Shaders)
* Vanilla JavaScript, HTML, CSS (Zero-dependency UI)

## ⚙️ Quickstart

To run this intelligence platform locally on your machine:

1. **Clone the repository:**

	```bash
	git clone https://github.com/Forest-code-ai/Space.git
	cd Space/vibe
	```

2. **Create/activate the virtual environment & install dependencies (PowerShell):**

	```powershell
	./scripts/bootstrap.ps1
	. ./.venv/Scripts/Activate.ps1
	pip install -r requirements.txt
	```

3. **Spin up the data engine and web server:**

	```powershell
	python -m vibe
	```

4. **Initialize the uplink:**

	Open your browser and navigate to http://localhost:8000.

Built as a proof-of-concept for geospatial data pipelines and multi-agent AI development.

---

If you want to push README updates to your portfolio:

```bash
git add README.md
git commit -m "docs: added comprehensive README outlining spatial architecture"
git push
```
