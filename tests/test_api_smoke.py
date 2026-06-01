import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from starlette.testclient import TestClient

import app.main as main_mod
from app.settings import Settings
from app.services.ai import (
    _build_chat_prompt,
    _describe_weather_code,
    _parse_gemini_translation_response,
    _requested_context,
)
from app.services.directions import route_cache
from app.services.ai import street_view_cache, weather_cache


class ApiSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main_mod.app)
        self.original_mode = main_mod.settings.AI_MODE
        self.original_gemini_key = main_mod.settings.GEMINI_API_KEY
        main_mod.settings.AI_MODE = "mock"
        main_mod.settings.GEMINI_API_KEY = None

    def tearDown(self) -> None:
        main_mod.settings.AI_MODE = self.original_mode
        main_mod.settings.GEMINI_API_KEY = self.original_gemini_key

    def test_index_ok(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("content-type", ""))

    def test_health_ok(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn(body.get("status"), {"ok", "degraded"})
        self.assertIn("ai_mode", body)

    def test_metrics_exposes_latency_and_cache_sections(self):
        route_cache.clear()
        weather_cache.clear()
        street_view_cache.clear()

        self.client.get("/")
        res = self.client.get("/metrics")

        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body.get("status"), "ok")
        self.assertIn("metrics", body)
        self.assertIn("cache", body)
        self.assertIn("routes", body["cache"])
        self.assertIn("weather", body["cache"])
        self.assertIn("street_view", body["cache"])

    def test_chat_mock_success(self):
        res = self.client.post(
            "/api/chat",
            json={
                "message": "What now?",
                "intent": "navigate",
                "context": {
                    "route_loaded": True,
                    "running": True,
                    "nav_primary": "Turn right",
                    "nav_secondary": "25 m",
                    "eta_min": 3,
                    "remaining_m": 220,
                },
            },
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body.get("mode"), "mock")
        self.assertTrue(body.get("reply"))

    def test_translate_mock_success(self):
        res = self.client.post(
            "/api/translate",
            json={"text": "Where is the station?", "target_lang": "es"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body.get("mode"), "mock")
        self.assertTrue(body.get("translated_text"))

    def test_translate_provider_error_becomes_502(self):
        async def fake_translate(*_args, **_kwargs):
            raise main_mod.AIServiceError("Gemini request failed: API key not valid.")

        main_mod.settings.AI_MODE = "gemini"
        main_mod.settings.GEMINI_API_KEY = "bad-key"

        try:
            with patch.object(main_mod, "gemini_translate", fake_translate):
                res = self.client.post(
                    "/api/translate",
                    json={"text": "hello", "target_lang": "en"},
                )
        finally:
            main_mod.settings.AI_MODE = "mock"
            main_mod.settings.GEMINI_API_KEY = None

        self.assertEqual(res.status_code, 502)
        detail = res.json().get("detail", {})
        self.assertIn("Gemini request failed", detail.get("message", ""))
        self.assertEqual(detail.get("code"), "AI_TRANSLATION_FAILED")

    def test_chat_uses_gemini_with_maps_context(self):
        async def fake_chat(gemini_key, model, google_api_key, message, context=None, intent=None):
            self.assertEqual(gemini_key, "good-key")
            self.assertEqual(model, "gemini-test")
            self.assertEqual(google_api_key, "maps-key")
            self.assertEqual(message, "What time is it?")
            self.assertEqual(context.get("current_address"), "5th Ave, New York, NY")
            return {"reply": "It is 3:15 PM.", "mode": "gemini"}

        main_mod.settings.AI_MODE = "gemini"
        main_mod.settings.GEMINI_API_KEY = "good-key"
        original_model = main_mod.settings.GEMINI_MODEL
        original_maps_key = main_mod.settings.GOOGLE_MAPS_API_KEY
        main_mod.settings.GEMINI_MODEL = "gemini-test"
        main_mod.settings.GOOGLE_MAPS_API_KEY = "maps-key"

        try:
            with patch.object(main_mod, "gemini_chat", fake_chat):
                res = self.client.post(
                    "/api/chat",
                    json={
                        "message": "What time is it?",
                        "context": {
                            "current_address": "5th Ave, New York, NY",
                            "current_position": {"lat": 40.758, "lng": -73.9855},
                        },
                    },
                )
        finally:
            main_mod.settings.AI_MODE = "mock"
            main_mod.settings.GEMINI_API_KEY = None
            main_mod.settings.GEMINI_MODEL = original_model
            main_mod.settings.GOOGLE_MAPS_API_KEY = original_maps_key

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("mode"), "gemini")

    def test_gemini_translation_json_is_reduced_to_plain_text(self):
        detected, translated = _parse_gemini_translation_response(
            '```json\n{"detected_source_lang":"es","translated_text":"Hello there"}\n```',
            "auto",
        )
        self.assertEqual(detected, "es")
        self.assertEqual(translated, "Hello there")

    def test_chat_prompt_includes_live_context(self):
        prompt = _build_chat_prompt(
            "What am I looking at?",
            {
                "route_loaded": True,
                "running": False,
                "nav_primary": "Turn right on Broadway",
                "nav_secondary": "35 m",
                "eta_min": 4,
                "remaining_m": 220,
                "current_address": "1560 Broadway, New York, NY",
                "current_position": {"lat": 40.758, "lng": -73.9855},
                "street_view": {"heading": 95, "pitch": 5, "pano_id": "pano-123"},
                "weather": {
                    "summary": "partly cloudy",
                    "temperature_c": 11.2,
                    "apparent_temperature_c": 9.8,
                    "humidity_pct": 62,
                    "precipitation_mm": 0.0,
                    "wind_speed_kmh": 13.4,
                    "observed_at": "2026-03-12T14:15",
                },
                "nearby_places": [{"name": "Starbucks", "vicinity": "Broadway", "types": ["cafe"]}],
            },
            datetime.now(ZoneInfo("America/New_York")),
        )
        self.assertIn("Current address: 1560 Broadway, New York, NY", prompt)
        self.assertIn("Current weather: partly cloudy, temperature 11.2 C", prompt)
        self.assertIn("Nearby places: Starbucks | cafe | Broadway", prompt)
        self.assertIn("Use the supplied date/time directly for time questions.", prompt)

    def test_weather_code_description(self):
        self.assertEqual(_describe_weather_code(63), "moderate rain")
        self.assertEqual(_describe_weather_code(999), "unknown")

    def test_requested_context_flags(self):
        context = {"requested_context": {"needsWeather": True, "needsStreetView": False}}
        self.assertTrue(_requested_context(context, "needsWeather"))
        self.assertFalse(_requested_context(context, "needsStreetView"))

    def test_route_success_shape(self):
        async def fake_route(**_kwargs):
            return {
                "status": "OK",
                "total_distance_m": 100,
                "total_duration_s": 80,
                "route_options": [{"index": 0, "summary": "Fastest", "total_distance_m": 100, "total_duration_s": 80}],
                "selected_route_index": 0,
                "steps": [
                    {
                        "instruction_html": "Head north",
                        "distance_m": 100,
                        "duration_s": 80,
                        "maneuver": "turn-right",
                        "start_location": {"lat": 1.0, "lng": 1.0},
                        "end_location": {"lat": 1.1, "lng": 1.1},
                    }
                ],
                "overview_polyline": "abc",
            }

        with patch.object(main_mod, "get_walking_route_steps", fake_route):
            res = self.client.post(
                "/api/route",
                json={
                    "origin": {"lat": 40.0, "lng": -73.0},
                    "destination": {"lat": 40.1, "lng": -73.1},
                },
            )

        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body.get("status"), "OK")
        self.assertEqual(len(body.get("steps", [])), 1)
        self.assertEqual(body.get("selected_route_index"), 0)

    def test_route_failure_status_propagates(self):
        async def fake_route(**_kwargs):
            return {
                "status": "NOT_FOUND",
                "error_message": "No route",
                "steps": [],
            }

        with patch.object(main_mod, "get_walking_route_steps", fake_route):
            res = self.client.post(
                "/api/route",
                json={
                    "origin": {"lat": 1.0, "lng": 1.0},
                    "destination": {"lat": 2.0, "lng": 2.0},
                },
            )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("status"), "NOT_FOUND")

    def test_route_request_passes_waypoints_and_route_index(self):
        async def fake_route(**kwargs):
            self.assertEqual(kwargs["route_index"], 1)
            self.assertEqual(kwargs["waypoints"], [{"lat": 40.05, "lng": -73.05}])
            return {
                "status": "OK",
                "total_distance_m": 120,
                "total_duration_s": 90,
                "route_options": [
                    {"index": 0, "summary": "Route A", "total_distance_m": 120, "total_duration_s": 90},
                    {"index": 1, "summary": "Route B", "total_distance_m": 135, "total_duration_s": 100},
                ],
                "selected_route_index": 1,
                "steps": [],
                "overview_polyline": "abc",
            }

        with patch.object(main_mod, "get_walking_route_steps", fake_route):
            res = self.client.post(
                "/api/route",
                json={
                    "origin": {"lat": 40.0, "lng": -73.0},
                    "destination": {"lat": 40.1, "lng": -73.1},
                    "waypoints": [{"lat": 40.05, "lng": -73.05}],
                    "route_index": 1,
                },
            )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("selected_route_index"), 1)

    def test_route_service_error_becomes_structured_http_error(self):
        async def fake_route(**_kwargs):
            raise main_mod.RouteServiceError(
                "Requested route option is out of range.",
                code="ROUTE_INDEX_INVALID",
                retryable=False,
            )

        with patch.object(main_mod, "get_walking_route_steps", fake_route):
            res = self.client.post(
                "/api/route",
                json={
                    "origin": {"lat": 40.0, "lng": -73.0},
                    "destination": {"lat": 40.1, "lng": -73.1},
                    "route_index": 99,
                },
            )

        self.assertEqual(res.status_code, 400)
        detail = res.json().get("detail", {})
        self.assertEqual(detail.get("code"), "ROUTE_INDEX_INVALID")
        self.assertEqual(detail.get("retryable"), False)

    def test_route_validation_error(self):
        res = self.client.post("/api/route", json={"origin": {"lat": 1.0, "lng": 1.0}})
        self.assertEqual(res.status_code, 422)

    def test_runtime_config_validation_messages(self):
        cfg = Settings(GOOGLE_MAPS_API_KEY="", AI_MODE="gemini", GEMINI_API_KEY=None)
        issues = cfg.runtime_issues()
        self.assertTrue(any("GOOGLE_MAPS_API_KEY" in x for x in issues))
        self.assertTrue(any("GEMINI_API_KEY" in x for x in issues))

    def test_runtime_config_validation_messages_for_gemini(self):
        cfg = Settings(GOOGLE_MAPS_API_KEY="maps-key", AI_MODE="gemini", GEMINI_API_KEY=None)
        issues = cfg.runtime_issues()
        self.assertTrue(any("GEMINI_API_KEY" in x for x in issues))


if __name__ == "__main__":
    unittest.main()
