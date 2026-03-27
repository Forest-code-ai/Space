"""Comprehensive tests for vibe/main.py."""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest
import requests as req
from fastapi.testclient import TestClient

from vibe.main import (
    _ecef_to_geodetic_wgs84,
    _gmst_radians,
    _parse_tle,
    _teme_to_ecef,
    app,
)

client = TestClient(app)

# ---------------------------------------------------------------------------
# _parse_tle
# ---------------------------------------------------------------------------

SAMPLE_TLE_BLOCK = """\
ISS (ZARYA)
1 25544U 98067A   21275.52195702  .00001448  00000-0  33784-4 0  9993
2 25544  51.6442 213.7130 0003460 111.4637 248.6832 15.48866526304799
"""

SAMPLE_TLE_TWO_SATS = """\
ISS (ZARYA)
1 25544U 98067A   21275.52195702  .00001448  00000-0  33784-4 0  9993
2 25544  51.6442 213.7130 0003460 111.4637 248.6832 15.48866526304799
HUBBLE
1 20580U 90037B   21275.46561156  .00001267  00000-0  62673-4 0  9997
2 20580  28.4691 275.4640 0002745 223.9779 136.1009 15.09641296  4768
"""


class TestParseTle:
    def test_single_satellite(self):
        result = _parse_tle(SAMPLE_TLE_BLOCK)
        assert len(result) == 1
        name, l1, l2 = result[0]
        assert name == "ISS (ZARYA)"
        assert l1.startswith("1 ")
        assert l2.startswith("2 ")

    def test_multiple_satellites(self):
        result = _parse_tle(SAMPLE_TLE_TWO_SATS)
        assert len(result) == 2
        assert result[0][0] == "ISS (ZARYA)"
        assert result[1][0] == "HUBBLE"

    def test_empty_string(self):
        assert _parse_tle("") == []

    def test_blank_lines_only(self):
        assert _parse_tle("\n\n\n") == []

    def test_incomplete_tle_missing_second_line(self):
        # Only a name and one TLE line — no valid triple can be formed
        text = "MYSAT\n1 25544U 98067A   21275.52  .00001448  00000-0  33784-4 0  9993\n"
        assert _parse_tle(text) == []

    def test_lines_not_starting_with_tle_markers(self):
        # Name present but lines don't start with "1 " / "2 "
        text = "MYSAT\nXXXXXXXXXXXXXXXXXXXXXXXXX\nYYYYYYYYYYYYYYYYYYYYYYYYY\n"
        assert _parse_tle(text) == []

    def test_extra_blank_lines_ignored(self):
        text = "\n\n" + SAMPLE_TLE_BLOCK + "\n\n"
        result = _parse_tle(text)
        assert len(result) == 1

    def test_returns_list_of_tuples(self):
        result = _parse_tle(SAMPLE_TLE_BLOCK)
        assert isinstance(result, list)
        assert isinstance(result[0], tuple)
        assert len(result[0]) == 3

    def test_leading_trailing_whitespace_stripped(self):
        text = "  ISS (ZARYA)  \n  1 25544U 98067A   21275.52195702  .00001448  00000-0  33784-4 0  9993  \n  2 25544  51.6442 213.7130 0003460 111.4637 248.6832 15.48866526304799  \n"
        result = _parse_tle(text)
        assert len(result) == 1
        name, l1, l2 = result[0]
        assert name == "ISS (ZARYA)"
        assert l1.startswith("1 ")
        assert l2.startswith("2 ")

    def test_only_two_lines_no_triple(self):
        # Two lines total: impossible to form a (name, l1, l2) triple
        text = "1 25544U ...\n2 25544 ...\n"
        assert _parse_tle(text) == []


# ---------------------------------------------------------------------------
# _gmst_radians
# ---------------------------------------------------------------------------

class TestGmstRadians:
    def test_j2000_epoch(self):
        # At J2000 (JD 2451545.0), GMST ≈ 280.46° → ≈ 4.894 rad
        gmst = _gmst_radians(2451545.0)
        expected = math.radians(280.46061837 % 360.0)
        assert abs(gmst - expected) < 1e-6

    def test_output_in_zero_to_2pi(self):
        for jd in [2451545.0, 2451545.5, 2460000.0, 2440000.0]:
            gmst = _gmst_radians(jd)
            assert 0.0 <= gmst < 2 * math.pi, f"GMST {gmst} out of [0, 2π) for JD {jd}"

    def test_returns_float(self):
        assert isinstance(_gmst_radians(2451545.0), float)

    def test_different_epochs_differ(self):
        gmst1 = _gmst_radians(2451545.0)
        gmst2 = _gmst_radians(2451545.5)
        assert gmst1 != gmst2


# ---------------------------------------------------------------------------
# _teme_to_ecef
# ---------------------------------------------------------------------------

class TestTemeToEcef:
    def test_identity_at_theta_zero(self):
        # When GMST ≈ 0, the rotation is nearly identity (x stays x, y stays y)
        # Find a JD where GMST ≈ 0
        # We just verify the math: patch _gmst_radians to return 0.
        with patch("vibe.main._gmst_radians", return_value=0.0):
            x_in, y_in, z_in = 1000.0, 2000.0, 3000.0
            x_out, y_out, z_out = _teme_to_ecef((x_in, y_in, z_in), 0.0)
            assert abs(x_out - x_in) < 1e-9
            assert abs(y_out - y_in) < 1e-9
            assert abs(z_out - z_in) < 1e-9

    def test_z_component_unchanged(self):
        with patch("vibe.main._gmst_radians", return_value=0.5):
            z_in = 12345.0
            _, _, z_out = _teme_to_ecef((1.0, 2.0, z_in), 0.0)
            assert abs(z_out - z_in) < 1e-9

    def test_rotation_at_pi_over_2(self):
        # At theta = π/2: x_ecef = cos·x + sin·y = 0·x + 1·y = y
        #                  y_ecef = -sin·x + cos·y = -x + 0  = -x
        with patch("vibe.main._gmst_radians", return_value=math.pi / 2):
            x_in, y_in, z_in = 3.0, 4.0, 5.0
            x_out, y_out, z_out = _teme_to_ecef((x_in, y_in, z_in), 0.0)
            assert abs(x_out - y_in) < 1e-9
            assert abs(y_out - (-x_in)) < 1e-9
            assert abs(z_out - z_in) < 1e-9

    def test_rotation_at_pi(self):
        # At theta = π: x_ecef = -x, y_ecef = y (cos=-1, sin≈0)
        with patch("vibe.main._gmst_radians", return_value=math.pi):
            x_in, y_in, z_in = 3.0, 4.0, 5.0
            x_out, y_out, z_out = _teme_to_ecef((x_in, y_in, z_in), 0.0)
            assert abs(x_out - (-x_in)) < 1e-9
            assert abs(y_out - (-y_in)) < 1e-9
            assert abs(z_out - z_in) < 1e-9

    def test_returns_tuple_of_three_floats(self):
        with patch("vibe.main._gmst_radians", return_value=0.0):
            result = _teme_to_ecef((1.0, 2.0, 3.0), 0.0)
            assert isinstance(result, tuple)
            assert len(result) == 3


# ---------------------------------------------------------------------------
# _ecef_to_geodetic_wgs84
# ---------------------------------------------------------------------------

class TestEcefToGeodeticWgs84:
    # WGS84 constants
    A = 6378137.0
    F = 1.0 / 298.257223563
    B = A * (1.0 - F)  # semi-minor axis

    def test_equator_prime_meridian(self):
        # Point on equator at prime meridian (x=a, y=0, z=0)
        lat, lon, alt = _ecef_to_geodetic_wgs84((self.A, 0.0, 0.0))
        assert abs(lat) < 1e-6
        assert abs(lon) < 1e-6
        assert abs(alt) < 1.0  # within 1 m of the surface

    def test_equator_90_degrees_east(self):
        lat, lon, alt = _ecef_to_geodetic_wgs84((0.0, self.A, 0.0))
        assert abs(lat) < 1e-6
        assert abs(lon - 90.0) < 1e-6
        assert abs(alt) < 1.0

    def test_north_pole(self):
        # North pole: (0, 0, b)
        lat, lon, alt = _ecef_to_geodetic_wgs84((0.0, 0.0, self.B))
        assert abs(lat - 90.0) < 1e-3
        assert abs(alt) < 1.0

    def test_south_pole(self):
        lat, lon, alt = _ecef_to_geodetic_wgs84((0.0, 0.0, -self.B))
        assert abs(lat + 90.0) < 1e-3

    def test_near_zero_p_positive_z(self):
        # p < 1e-9: copysign(π/2, z) path
        lat, lon, alt = _ecef_to_geodetic_wgs84((0.0, 0.0, 7000000.0))
        assert abs(lat - 90.0) < 1e-6

    def test_near_zero_p_negative_z(self):
        lat, lon, alt = _ecef_to_geodetic_wgs84((0.0, 0.0, -7000000.0))
        assert abs(lat + 90.0) < 1e-6

    def test_above_surface(self):
        # Point 500 km above the equator at prime meridian
        alt_target = 500_000.0
        r = self.A + alt_target
        lat, lon, alt = _ecef_to_geodetic_wgs84((r, 0.0, 0.0))
        assert abs(lat) < 1e-6
        assert abs(lon) < 1e-6
        assert abs(alt - alt_target) < 1.0

    def test_returns_tuple_of_three(self):
        result = _ecef_to_geodetic_wgs84((self.A, 0.0, 0.0))
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_longitude_range(self):
        # Longitude should be in [-180, 180]
        for x, y, z in [(self.A, 0, 0), (0, self.A, 0), (-self.A, 0, 0), (0, -self.A, 0)]:
            _, lon, _ = _ecef_to_geodetic_wgs84((x, y, z))
            assert -180.0 <= lon <= 180.0


# ---------------------------------------------------------------------------
# API endpoint: GET /
# ---------------------------------------------------------------------------

class TestRootEndpoint:
    def test_returns_200(self):
        response = client.get("/")
        assert response.status_code == 200

    def test_content_type_html(self):
        response = client.get("/")
        assert "text/html" in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# API endpoint: GET /favicon.ico
# ---------------------------------------------------------------------------

class TestFaviconEndpoint:
    def test_returns_204(self):
        response = client.get("/favicon.ico")
        assert response.status_code == 204


# ---------------------------------------------------------------------------
# API endpoint: GET /api/flights
# ---------------------------------------------------------------------------

def _make_state(
    icao24="abc123",
    callsign="TEST01",
    origin_country="Canada",
    time_position=1234567890,
    last_contact=1234567890,
    longitude=-79.5,
    latitude=43.8,
    baro_altitude=10000.0,
    on_ground=False,
    velocity=250.0,
    true_track=90.0,
    vertical_rate=0.0,
    sensors=None,
    geo_altitude=10200.0,
    squawk=None,
    spi=False,
    position_source=0,
):
    return [
        icao24, callsign, origin_country, time_position, last_contact,
        longitude, latitude, baro_altitude, on_ground, velocity,
        true_track, vertical_rate, sensors, geo_altitude, squawk, spi, position_source,
    ]


class TestFlightsEndpoint:
    def test_success_returns_flights(self):
        state = _make_state()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"time": 1234567890, "states": [state]}
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/flights")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert len(data["flights"]) == 1
        flight = data["flights"][0]
        assert flight["icao24"] == "abc123"
        assert flight["callsign"] == "TEST01"
        assert flight["lat"] == 43.8
        assert flight["lon"] == -79.5

    def test_flight_fields_present(self):
        state = _make_state()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"time": 1000, "states": [state]}
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/flights")

        flight = response.json()["flights"][0]
        for key in ("icao24", "callsign", "origin_country", "time_position",
                    "last_contact", "lon", "lat", "baro_alt_m", "geo_alt_m",
                    "on_ground", "velocity_mps", "track_deg", "vertical_rate_mps"):
            assert key in flight, f"Missing field: {key}"

    def test_filters_out_null_lat(self):
        state = _make_state(latitude=None)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"time": 1000, "states": [state]}
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/flights")

        assert response.json()["count"] == 0

    def test_filters_out_null_lon(self):
        state = _make_state(longitude=None)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"time": 1000, "states": [state]}
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/flights")

        assert response.json()["count"] == 0

    def test_empty_states_list(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"time": 1000, "states": []}
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/flights")

        data = response.json()
        assert data["count"] == 0
        assert data["flights"] == []

    def test_null_states_treated_as_empty(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"time": 1000, "states": None}
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/flights")

        assert response.json()["count"] == 0

    def test_missing_states_key(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"time": 1000}
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/flights")

        assert response.json()["count"] == 0

    def test_request_exception_returns_502(self):
        with patch("vibe.main.requests.get", side_effect=req.RequestException("timeout")):
            response = client.get("/api/flights")

        assert response.status_code == 502
        assert "OpenSky" in response.json()["detail"]

    def test_http_error_returns_502(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.HTTPError("404")

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/flights")

        assert response.status_code == 502

    def test_response_includes_bbox(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"time": 1000, "states": []}
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/flights")

        bbox = response.json()["bbox"]
        assert bbox["lamin"] == 43.5
        assert bbox["lamax"] == 45.5
        assert bbox["lomin"] == -80.0
        assert bbox["lomax"] == -76.0

    def test_callsign_whitespace_stripped(self):
        state = _make_state(callsign="  STRIP  ")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"time": 1000, "states": [state]}
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/flights")

        assert response.json()["flights"][0]["callsign"] == "STRIP"

    def test_null_callsign_becomes_empty_string(self):
        state = _make_state(callsign=None)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"time": 1000, "states": [state]}
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/flights")

        assert response.json()["flights"][0]["callsign"] == ""

    def test_multiple_flights_mixed_validity(self):
        valid = _make_state(icao24="valid1")
        no_lat = _make_state(icao24="nolat1", latitude=None)
        no_lon = _make_state(icao24="nolon1", longitude=None)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"time": 1000, "states": [valid, no_lat, no_lon]}
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/flights")

        data = response.json()
        assert data["count"] == 1
        assert data["flights"][0]["icao24"] == "valid1"


# ---------------------------------------------------------------------------
# API endpoint: GET /api/satellites
# ---------------------------------------------------------------------------

VALID_TLE_TEXT = """\
ISS (ZARYA)
1 25544U 98067A   21275.52195702  .00001448  00000-0  33784-4 0  9993
2 25544  51.6442 213.7130 0003460 111.4637 248.6832 15.48866526304799
"""


class TestSatellitesEndpoint:
    def test_success_returns_satellites(self):
        mock_resp = MagicMock()
        mock_resp.text = VALID_TLE_TEXT
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/satellites")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        sat = data[0]
        assert sat["name"] == "ISS (ZARYA)"
        assert "lat" in sat
        assert "lon" in sat
        assert "alt" in sat

    def test_satellite_lat_lon_ranges(self):
        mock_resp = MagicMock()
        mock_resp.text = VALID_TLE_TEXT
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/satellites")

        sat = response.json()[0]
        assert -90.0 <= sat["lat"] <= 90.0
        assert -180.0 <= sat["lon"] <= 180.0
        assert sat["alt"] > 0

    def test_request_exception_returns_502(self):
        with patch("vibe.main.requests.get", side_effect=req.RequestException("timeout")):
            response = client.get("/api/satellites")

        assert response.status_code == 502
        assert "CelesTrak" in response.json()["detail"]

    def test_http_error_returns_502(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.HTTPError("500")

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/satellites")

        assert response.status_code == 502

    def test_empty_tle_returns_empty_list(self):
        mock_resp = MagicMock()
        mock_resp.text = ""
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/satellites")

        assert response.status_code == 200
        assert response.json() == []

    def test_sgp4_nonzero_error_skips_satellite(self):
        """Satellite propagation error (err != 0) should be silently skipped."""
        mock_resp = MagicMock()
        mock_resp.text = VALID_TLE_TEXT
        mock_resp.raise_for_status.return_value = None

        mock_sat = MagicMock()
        mock_sat.sgp4.return_value = (1, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))  # err=1

        with patch("vibe.main.requests.get", return_value=mock_resp), \
             patch("vibe.main.Satrec.twoline2rv", return_value=mock_sat):
            response = client.get("/api/satellites")

        assert response.status_code == 200
        assert response.json() == []

    def test_sgp4_exception_skips_satellite(self):
        """Exception during TLE parsing/propagation should be silently skipped."""
        mock_resp = MagicMock()
        mock_resp.text = VALID_TLE_TEXT
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp), \
             patch("vibe.main.Satrec.twoline2rv", side_effect=Exception("bad TLE")):
            response = client.get("/api/satellites")

        assert response.status_code == 200
        assert response.json() == []

    def test_multiple_satellites(self):
        mock_resp = MagicMock()
        mock_resp.text = VALID_TLE_TEXT * 2  # two identical blocks = two sats
        mock_resp.raise_for_status.return_value = None

        # Two-block TLE won't naturally have unique names, but _parse_tle
        # will still return two triples.
        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/satellites")

        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_satellite_altitude_is_float(self):
        mock_resp = MagicMock()
        mock_resp.text = VALID_TLE_TEXT
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/satellites")

        sat = response.json()[0]
        assert isinstance(sat["alt"], float)

    def test_satellite_name_present(self):
        mock_resp = MagicMock()
        mock_resp.text = VALID_TLE_TEXT
        mock_resp.raise_for_status.return_value = None

        with patch("vibe.main.requests.get", return_value=mock_resp):
            response = client.get("/api/satellites")

        assert response.json()[0]["name"] == "ISS (ZARYA)"
