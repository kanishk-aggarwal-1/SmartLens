from __future__ import annotations

from typing import Any
import httpx

async def get_walking_route_steps(
    google_api_key: str,
    origin: dict[str, float],
    destination: dict[str, float],
) -> dict[str, Any]:
    """
    Calls Google Directions API (REST) and returns simplified steps.
    origin/destination are dicts: {"lat":..., "lng":...}
    """
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": f"{origin['lat']},{origin['lng']}",
        "destination": f"{destination['lat']},{destination['lng']}",
        "mode": "walking",
        "key": google_api_key,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    if data.get("status") != "OK":
        return {
            "status": data.get("status"),
            "error_message": data.get("error_message"),
            "steps": [],
        }

    route = data["routes"][0]
    leg = route["legs"][0]
    steps = []
    detailed_path = []
    for s in leg["steps"]:
        steps.append({
            "instruction_html": s.get("html_instructions", ""),
            "distance_m": int(s["distance"]["value"]),
            "duration_s": int(s["duration"]["value"]),
            "maneuver": s.get("maneuver"),
            "start_location": s.get("start_location"),
            "end_location": s.get("end_location"),
            "polyline": s.get("polyline", {}).get("points"),
        })

        if s.get("polyline", {}).get("points"):
            detailed_path.append(s["polyline"]["points"])

    return {
        "status": "OK",
        "summary": route.get("summary"),
        "total_distance_m": int(leg["distance"]["value"]),
        "total_duration_s": int(leg["duration"]["value"]),
        "steps": steps,
        "overview_polyline": route.get("overview_polyline", {}).get("points"),
        "detailed_path": detailed_path,
    }
