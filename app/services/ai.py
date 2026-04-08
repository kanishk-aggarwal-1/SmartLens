from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

class AIServiceError(Exception):
    pass


PHRASEBOOK: dict[str, dict[str, str]] = {
    "where is the station?": {
        "es": "Donde esta la estacion?",
        "fr": "Ou est la gare?",
        "de": "Wo ist der Bahnhof?",
        "hi": "Station kahan hai?",
    },
    "i need help": {
        "es": "Necesito ayuda",
        "fr": "J'ai besoin d'aide",
        "de": "Ich brauche Hilfe",
        "hi": "Mujhe madad chahiye",
    },
    "how much does this cost?": {
        "es": "Cuanto cuesta esto?",
        "fr": "Combien ca coute?",
        "de": "Wie viel kostet das?",
        "hi": "Yeh kitne ka hai?",
    },
}


def mock_translate(text: str, target_lang: str, source_lang: str = "auto") -> dict[str, Any]:
    normalized = text.strip().lower()
    phrase = PHRASEBOOK.get(normalized, {})
    translated = phrase.get(target_lang)
    detected_source_lang = source_lang if source_lang != "auto" else "auto"

    if not translated:
        translated = f"[{target_lang.upper()}] {text}"

    return {
        "target_lang": target_lang,
        "source_lang": source_lang,
        "detected_source_lang": detected_source_lang,
        "source_text": text,
        "translated_text": translated,
        "mode": "mock",
        "phrasebook_match": bool(phrase),
    }


def mock_chat(message: str, context: dict[str, Any] | None = None, intent: str | None = None) -> dict[str, Any]:
    context = context or {}

    nav_primary = str(context.get("nav_primary") or "")
    nav_secondary = str(context.get("nav_secondary") or "")
    eta_min = context.get("eta_min")
    remaining_m = context.get("remaining_m")
    route_loaded = bool(context.get("route_loaded"))
    running = bool(context.get("running"))

    if intent == "navigate":
        if route_loaded:
            eta_text = f" ETA: {eta_min} min." if eta_min else ""
            remaining_text = f" Remaining: {remaining_m} m." if remaining_m else ""
            status = "in progress" if running else "paused"
            reply = (
                f"Navigation is {status}. Now: {nav_primary or 'Follow the highlighted route.'}"
                f" {nav_secondary}".strip()
                + remaining_text
                + eta_text
            )
        else:
            reply = "No route is loaded yet. Set start and destination, then load route."
        return {"reply": reply.strip(), "mode": "mock"}

    if intent == "translate":
        return {
            "reply": "Use phrase chips for quick presets, or enter text and choose a target language.",
            "mode": "mock",
        }

    if intent == "assist":
        return {
            "reply": "Keep your head up, stay on sidewalks, and pause the demo before crossing busy streets.",
            "mode": "mock",
        }

    canned = [
        "I can help with navigation, translation, or quick questions.",
        "If you are walking, keep your head up and I will handle directions.",
        "Paste text for translation or press an intent chip for a guided action.",
    ]

    if route_loaded:
        context_reply = f"Current step: {nav_primary or 'Follow route'} {nav_secondary}".strip()
        eta_text = f" ETA about {eta_min} min." if eta_min else ""
        return {"reply": context_reply + eta_text, "mode": "mock"}

    reply = canned[hash(message) % len(canned)]
    return {"reply": reply, "mode": "mock"}


def _gemini_generate_sync(
    gemini_key: str,
    model: str,
    system_instruction: str,
    contents: Any,
) -> str:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types

    try:
        client = genai.Client(api_key=gemini_key)
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_instruction),
        )
    except genai_errors.ClientError as exc:
        raise AIServiceError(f"Gemini request failed: {exc}") from exc
    except Exception as exc:
        raise AIServiceError("Gemini request failed.") from exc

    return (response.text or "").strip()


def _parse_gemini_translation_response(raw_text: str, source_lang: str) -> tuple[str, str]:
    cleaned = (raw_text or "").strip()
    detected_source_lang = source_lang
    translated_text = cleaned

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
        detected_source_lang = parsed.get("detected_source_lang") or detected_source_lang
        translated_text = parsed.get("translated_text") or translated_text
        return detected_source_lang, translated_text.strip()
    except Exception:
        pass

    translated_match = re.search(r'"translated_text"\s*:\s*"(.+?)"', cleaned, flags=re.DOTALL)
    detected_match = re.search(r'"detected_source_lang"\s*:\s*"(.+?)"', cleaned, flags=re.DOTALL)

    if detected_match:
        detected_source_lang = detected_match.group(1).strip()
    if translated_match:
        translated_text = translated_match.group(1).encode("utf-8").decode("unicode_escape")

    if (
        len(translated_text) >= 2 and
        ((translated_text.startswith('"') and translated_text.endswith('"')) or
         (translated_text.startswith("'") and translated_text.endswith("'")))
    ):
        translated_text = translated_text[1:-1]

    return detected_source_lang, translated_text.strip()


def _format_nearby_places(context: dict[str, Any]) -> str:
    places = context.get("nearby_places") or []
    if not isinstance(places, list) or not places:
        return "Not available"

    summaries: list[str] = []
    for place in places[:5]:
        if not isinstance(place, dict):
            continue
        name = str(place.get("name") or "").strip()
        types = place.get("types") or []
        vicinity = str(place.get("vicinity") or "").strip()
        rating = place.get("rating")
        type_text = ", ".join(str(item) for item in types[:2] if item) if isinstance(types, list) else ""
        parts = [part for part in [name, type_text, vicinity] if part]
        if rating:
            parts.append(f"rating {rating}")
        if parts:
            summaries.append(" | ".join(parts))

    return "; ".join(summaries) if summaries else "Not available"


def _requested_context(context: dict[str, Any], key: str) -> bool:
    requested = context.get("requested_context") or {}
    return bool(requested.get(key))


def _describe_weather_code(code: Any) -> str:
    labels = {
        0: "clear sky",
        1: "mainly clear",
        2: "partly cloudy",
        3: "overcast",
        45: "fog",
        48: "depositing rime fog",
        51: "light drizzle",
        53: "moderate drizzle",
        55: "dense drizzle",
        56: "light freezing drizzle",
        57: "dense freezing drizzle",
        61: "slight rain",
        63: "moderate rain",
        65: "heavy rain",
        66: "light freezing rain",
        67: "heavy freezing rain",
        71: "slight snow",
        73: "moderate snow",
        75: "heavy snow",
        77: "snow grains",
        80: "slight rain showers",
        81: "moderate rain showers",
        82: "violent rain showers",
        85: "slight snow showers",
        86: "heavy snow showers",
        95: "thunderstorm",
        96: "thunderstorm with slight hail",
        99: "thunderstorm with heavy hail",
    }
    return labels.get(code, "unknown")


async def _fetch_weather_context(context: dict[str, Any]) -> dict[str, Any] | None:
    current_position = context.get("current_position") or {}
    lat = current_position.get("lat")
    lng = current_position.get("lng")
    if lat is None or lng is None:
        return None

    params = {
        "latitude": lat,
        "longitude": lng,
        "current": ",".join([
            "temperature_2m",
            "apparent_temperature",
            "relative_humidity_2m",
            "precipitation",
            "weather_code",
            "wind_speed_10m",
        ]),
        "timezone": "auto",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None

    current = payload.get("current") or {}
    if not current:
        return None

    weather_code = current.get("weather_code")
    return {
        "temperature_c": current.get("temperature_2m"),
        "apparent_temperature_c": current.get("apparent_temperature"),
        "humidity_pct": current.get("relative_humidity_2m"),
        "precipitation_mm": current.get("precipitation"),
        "wind_speed_kmh": current.get("wind_speed_10m"),
        "weather_code": weather_code,
        "summary": _describe_weather_code(weather_code),
        "observed_at": current.get("time"),
    }


def _build_chat_prompt(message: str, context: dict[str, Any], now: datetime) -> str:
    current_position = context.get("current_position") or {}
    street_view = context.get("street_view") or {}
    weather = context.get("weather") or {}
    local_date = now.strftime("%A, %B %d, %Y")
    local_time = now.strftime("%I:%M %p %Z").lstrip("0")

    prompt_parts = [
        f"User message: {message}",
        f"Current local date: {local_date}",
        f"Current local time: {local_time}",
        "Use the supplied date/time directly for time questions. Do not use placeholders like [current time].",
        f"Route loaded: {bool(context.get('route_loaded'))}",
        f"Demo running: {bool(context.get('running'))}",
        f"Current navigation instruction: {context.get('nav_primary') or 'Not available'}",
        f"Navigation secondary info: {context.get('nav_secondary') or 'Not available'}",
        f"ETA minutes: {context.get('eta_min') if context.get('eta_min') is not None else 'Not available'}",
        f"Remaining meters: {context.get('remaining_m') if context.get('remaining_m') is not None else 'Not available'}",
        f"Current address: {context.get('current_address') or 'Not available'}",
        (
            "Current coordinates: "
            f"{current_position.get('lat', 'Not available')}, {current_position.get('lng', 'Not available')}"
        ),
        (
            "Street View camera: "
            f"heading {street_view.get('heading', 'Not available')}, "
            f"pitch {street_view.get('pitch', 'Not available')}, "
            f"pano {street_view.get('pano_id') or 'Not available'}"
        ),
        (
            "Current weather: "
            f"{weather.get('summary', 'Not available')}, "
            f"temperature {weather.get('temperature_c', 'Not available')} C, "
            f"feels like {weather.get('apparent_temperature_c', 'Not available')} C, "
            f"humidity {weather.get('humidity_pct', 'Not available')}%, "
            f"precipitation {weather.get('precipitation_mm', 'Not available')} mm, "
            f"wind {weather.get('wind_speed_kmh', 'Not available')} km/h, "
            f"observed at {weather.get('observed_at', 'Not available')}"
        ),
        f"Nearby places: {_format_nearby_places(context)}",
        "If the image is available, use it to answer questions about what is visible ahead.",
        "If the user asks about weather, use the supplied weather context instead of guessing from the Street View image.",
        "Be concise and grounded in the provided context. If something is uncertain, say that directly.",
    ]
    return "\n".join(prompt_parts)


async def _fetch_street_view_image(
    google_api_key: str,
    context: dict[str, Any],
) -> bytes | None:
    current_position = context.get("current_position") or {}
    lat = current_position.get("lat")
    lng = current_position.get("lng")
    if lat is None or lng is None:
        return None

    street_view = context.get("street_view") or {}
    heading = street_view.get("heading", 0)
    pitch = street_view.get("pitch", 5)
    fov = street_view.get("fov", 90)

    params = {
        "size": "640x640",
        "location": f"{lat},{lng}",
        "heading": heading,
        "pitch": pitch,
        "fov": fov,
        "source": "outdoor",
        "key": google_api_key,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get("https://maps.googleapis.com/maps/api/streetview", params=params)
            response.raise_for_status()
        except Exception:
            return None

    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        return None
    return response.content


async def gemini_translate(
    gemini_key: str,
    model: str,
    text: str,
    source_lang: str,
    target_lang: str,
) -> dict[str, Any]:
    if source_lang == "auto":
        prompt = (
            f"Detect the source language, then translate the following text into {target_lang}. "
            "Return only the translated text. Do not return JSON, labels, markdown, or quotes.\n\n"
            f"Text:\n{text}"
        )
    else:
        prompt = (
            f"Translate the following text from {source_lang} into {target_lang}. "
            "Return only the translated text. Do not return JSON, labels, markdown, or quotes.\n\n"
            f"Text:\n{text}"
        )

    translated = await asyncio.to_thread(
        _gemini_generate_sync,
        gemini_key,
        model,
        "You are a precise, concise translation engine.",
        prompt,
    )
    detected_source_lang, translated_text = _parse_gemini_translation_response(
        translated,
        "auto" if source_lang == "auto" else source_lang,
    )

    return {
        "target_lang": target_lang,
        "source_lang": source_lang,
        "detected_source_lang": detected_source_lang,
        "source_text": text,
        "translated_text": translated_text,
        "mode": "gemini",
    }


async def gemini_chat(
    gemini_key: str,
    model: str,
    google_api_key: str,
    message: str,
    context: dict[str, Any] | None = None,
    intent: str | None = None,
) -> dict[str, Any]:
    from google.genai import types

    context = context or {}
    need_weather = _requested_context(context, "needsWeather")
    need_street_view = _requested_context(context, "needsStreetView")

    weather = await _fetch_weather_context(context) if need_weather else None
    if weather:
        context = {**context, "weather": weather}
    now = datetime.now(ZoneInfo("America/New_York"))
    prompt = _build_chat_prompt(message, context, now)
    contents: list[Any] = [types.Part.from_text(text=prompt)]

    street_view_image = await _fetch_street_view_image(google_api_key, context) if need_street_view else None
    if street_view_image:
        contents.append(types.Part.from_bytes(data=street_view_image, mime_type="image/jpeg"))

    reply = await asyncio.to_thread(
        _gemini_generate_sync,
        gemini_key,
        model,
        (
            "You are a smart-glasses assistant. Keep replies short, practical, and easy to act on while walking. "
            "Use the provided live context, nearby places, and image if present. "
            "If the user asks for the current time or date, answer using the provided local values exactly."
        ),
        contents,
    )
    return {"reply": reply, "mode": "gemini"}
