from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from ..settings import settings
from .cache import TTLCache
from .monitoring import logger, metrics


class RouteServiceError(Exception):
    def __init__(
        self,
        message: str,
        *,
        provider: str = "google_directions",
        code: str = "ROUTE_PROVIDER_ERROR",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.retryable = retryable


route_cache: TTLCache[dict[str, Any]] = TTLCache(
    name="routes",
    default_ttl_s=settings.ROUTE_CACHE_TTL_S,
)


def _route_cache_key(
    origin: dict[str, float],
    destination: dict[str, float],
    waypoints: list[dict[str, float]] | None,
    route_index: int,
    include_alternatives: bool,
) -> str:
    return json.dumps(
        {
            "origin": {
                "lat": round(float(origin["lat"]), 5),
                "lng": round(float(origin["lng"]), 5),
            },
            "destination": {
                "lat": round(float(destination["lat"]), 5),
                "lng": round(float(destination["lng"]), 5),
            },
            "waypoints": [
                {
                    "lat": round(float(point["lat"]), 5),
                    "lng": round(float(point["lng"]), 5),
                }
                for point in (waypoints or [])
            ],
            "route_index": route_index,
            "include_alternatives": include_alternatives,
        },
        sort_keys=True,
    )


async def _google_get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    attempts = max(1, settings.PROVIDER_RETRY_COUNT + 1)
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_S) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
            metrics.record_provider_status("google_directions", "ok")
            metrics.record_latency(
                "provider.google_directions",
                (time.perf_counter() - started) * 1000,
                success=True,
            )
            return data
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            metrics.record_provider_status("google_directions", f"error:{type(exc).__name__}")
            metrics.record_latency(
                "provider.google_directions",
                (time.perf_counter() - started) * 1000,
                success=False,
            )
            if attempt >= attempts:
                break
            await asyncio.sleep(0.15 * attempt)

    logger.warning("google_directions_request_failed error=%s", last_exc)
    raise RouteServiceError("Google Directions request failed.") from last_exc


def _normalize_steps(route: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    leg = route["legs"][0]
    steps: list[dict[str, Any]] = []
    detailed_path: list[str] = []
    for step in leg["steps"]:
        steps.append(
            {
                "instruction_html": step.get("html_instructions", ""),
                "distance_m": int(step["distance"]["value"]),
                "duration_s": int(step["duration"]["value"]),
                "maneuver": step.get("maneuver"),
                "start_location": step.get("start_location"),
                "end_location": step.get("end_location"),
                "polyline": step.get("polyline", {}).get("points"),
            }
        )

        if step.get("polyline", {}).get("points"):
            detailed_path.append(step["polyline"]["points"])

    return steps, detailed_path


def _route_option(route: dict[str, Any], index: int) -> dict[str, Any]:
    leg = route["legs"][0]
    warnings = route.get("warnings") or []
    return {
        "index": index,
        "summary": route.get("summary") or f"Route {index + 1}",
        "total_distance_m": int(leg["distance"]["value"]),
        "total_duration_s": int(leg["duration"]["value"]),
        "warnings": warnings,
    }


async def get_walking_route_steps(
    google_api_key: str,
    origin: dict[str, float],
    destination: dict[str, float],
    *,
    waypoints: list[dict[str, float]] | None = None,
    route_index: int = 0,
    include_alternatives: bool = True,
) -> dict[str, Any]:
    """
    Calls Google Directions API (REST) and returns simplified steps.
    origin/destination are dicts: {"lat":..., "lng":...}
    """
    cache_key = _route_cache_key(origin, destination, waypoints, route_index, include_alternatives)
    cached = route_cache.get(cache_key)
    if cached is not None:
        metrics.record_provider_status("google_directions_cache", "hit")
        return cached

    metrics.record_provider_status("google_directions_cache", "miss")
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": f"{origin['lat']},{origin['lng']}",
        "destination": f"{destination['lat']},{destination['lng']}",
        "mode": "walking",
        "alternatives": str(include_alternatives).lower(),
        "key": google_api_key,
    }
    if waypoints:
        params["waypoints"] = "|".join(f"{point['lat']},{point['lng']}" for point in waypoints)
    data = await _google_get_json(url, params)

    if data.get("status") != "OK":
        result = {
            "status": data.get("status"),
            "error_message": data.get("error_message"),
            "steps": [],
            "route_options": [],
        }
        route_cache.set(cache_key, result, ttl_s=30)
        return result

    routes = data.get("routes") or []
    if not routes:
        raise RouteServiceError(
            "Directions response did not contain any routes.",
            code="ROUTE_RESPONSE_INVALID",
            retryable=False,
        )

    if route_index < 0 or route_index >= len(routes):
        raise RouteServiceError(
            "Requested route option is out of range.",
            code="ROUTE_INDEX_INVALID",
            retryable=False,
        )

    route = routes[route_index]
    leg = route["legs"][0]
    steps, detailed_path = _normalize_steps(route)
    route_options = [_route_option(item, index) for index, item in enumerate(routes)]

    result = {
        "status": "OK",
        "summary": route.get("summary"),
        "total_distance_m": int(leg["distance"]["value"]),
        "total_duration_s": int(leg["duration"]["value"]),
        "steps": steps,
        "overview_polyline": route.get("overview_polyline", {}).get("points"),
        "detailed_path": detailed_path,
        "route_options": route_options,
        "selected_route_index": route_index,
        "waypoint_count": len(waypoints or []),
    }
    route_cache.set(cache_key, result)
    return result


def get_cache_stats() -> dict[str, dict[str, int | str]]:
    return {"routes": route_cache.stats()}
