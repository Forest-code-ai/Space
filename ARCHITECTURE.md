# Space Project Architecture

## Purpose

Space is a browser-based spatial intelligence prototype built to visualize live geospatial entities on a 3D globe. The system combines a lightweight Python backend with a Cesium-based frontend. Its goal is to remain easy to iterate on while avoiding brittle changes that break rendering, polling, or HUD behavior.

## System Boundaries

The project is split into two primary layers:

- **Backend:** data access and normalization
- **Frontend:** rendering, interaction, and visual state

This split must stay clear.

- The backend should fetch, normalize, and expose data.
- The frontend should display that data and manage user interaction.

## Backend Responsibilities

**Primary file:** `vibe/main.py`

The backend is responsible for:

- Serving the app
- Exposing API endpoints such as flights and satellites
- Fetching external data sources
- Normalizing raw external formats into clean JSON for the frontend
- Keeping third-party parsing logic out of the frontend

The backend should not contain UI rules, Cesium logic, shader logic, or presentation formatting.

### Current backend role by endpoint

- **Flights endpoint:** returns aircraft data in a frontend-friendly structure
- **Satellites endpoint:** fetches TLE data, propagates positions, and returns clean satellite coordinates
- **Static serving:** serves the frontend assets needed by the browser

## Frontend Responsibilities

**Primary file:** `static/index.html`

The frontend is responsible for:

- Initializing the Cesium viewer
- Configuring imagery and camera defaults
- Creating and updating entities
- Handling polling for live data
- Managing the tactical HUD
- Managing sensor mode state
- Managing click interactions and target locking
- Defining non-destructive post-process visual effects

The frontend should not do heavy parsing of raw external formats if the backend can normalize them first.

## Frontend Internal Structure

The single-page frontend should be organized into stable sections. Even if it remains one file, it should behave like a small modular system.

### 1) Viewer Setup

Contains:

- Cesium viewer initialization
- Imagery layer setup
- Default camera positioning
- Scene-level settings

This section should be edited only when changing base map behavior, startup camera, or scene-wide render settings.

### 2) Static Scene Elements

Contains:

- CCTV zone
- Fixed labels
- Persistent overlays
- Any static tactical markers

These elements should be isolated so they do not get mixed into live entity polling logic.

### 3) Live Data Polling

Contains:

- Fetch logic for flights
- Fetch logic for satellites
- Poll intervals
- Stale entity cleanup

This section should only manage data refresh and entity synchronization.

### 4) Entity Registry and Update Logic

Contains:

- Aircraft entity map
- Satellite entity map
- Create-or-update logic
- Style defaults for each entity type

This section is the source of truth for how live entities are created and refreshed.

### 5) HUD State

Contains:

- Current sensor mode
- Mouse targeting coordinates
- Locked target state
- Any values shown in the targeting panel

This section should not directly fetch data. It should consume current app state.

### 6) Sensor Modes and Shaders

Contains:

- STANDARD mode behavior
- NIGHT VISION stage
- THERMAL stage
- Button state updates

This section must remain isolated from data polling and entity creation. Shader changes should not alter app logic.

### 7) Interaction Layer

Contains:

- Click picking
- Hover logic (if added)
- Lock and clear target behavior
- Camera or inspect interactions

This section controls how the user interacts with entities, not how those entities are fetched.

## State Model

The app should maintain a small, explicit state model.

Recommended state objects:

- `currentSensorMode`
- `lockedTarget`
- `satellitesById`
- `flightsById`
- `cctvZoneConfig`

All major UI behavior should flow from these known state holders. Avoid hidden state spread across unrelated functions.

## Design Principles

### Keep the backend thin and deterministic

The backend should do reliable transformation work, not frontend improvisation.

### Keep the frontend stateful but readable

The frontend can own interactive state, but that state should be centralized and named clearly.

### Avoid cross-coupling

A fix in one layer should not quietly rewrite another unrelated layer. For example:

- Shader work should not rewrite polling logic
- HUD work should not rewrite imagery setup
- CCTV styling should not rewrite satellite logic

### Prefer visible defaults

The app should always boot into a clearly visible and debuggable state. STANDARD mode and a working imagery layer are the baseline reference.

## Change Strategy

When expanding the project, changes should usually land in one of these categories:

- Data ingestion
- Entity rendering
- HUD interaction
- Visual effects
- Static scene styling

Changes should stay inside one category unless there is a clear reason to cross boundaries.

## Near-Term Extension Points

The cleanest next expansions are:

- Target lock and intercept workflows
- Click-to-inspect entity panels
- Alerting logic based on geographic conditions
- Refined styling for tactical overlays
- Additional sensor modes that do not alter core state logic

## Non-Goals (For Now)

The project does not currently need:

- A large frontend framework
- Deep backend abstraction
- Complex authentication
- Full production security architecture
- Multi-file frontend refactor before the interaction model stabilizes

The priority is controlled iteration, not premature complexity.

## Architecture Rule

If a change makes the project harder to explain in one minute, it is probably too broad for a single pass and should be split into smaller edits.
