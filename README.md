# SmartLens

SmartLens is a FastAPI web app that simulates a smart-glasses interface in the browser. It combines Google Maps walking directions, dual Street View lenses, live navigation UI, translation, chat, voice input/output, and Gemini-powered contextual assistance.

## Features

- Dual lens Street View layout
- Walking route loading from start and destination
- Turn queue and instruction HUD on the right lens
- GPS map with live current-position marker
- Text and voice translation
- Text and voice AI chat
- Gemini chat with live context:
  - current location
  - nearby places
  - Street View visual context
  - date/time
  - weather

## Tech Stack

- FastAPI
- Jinja2 templates
- Vanilla JavaScript
- Google Maps JavaScript API
- Google Directions / Street View / Places
- Gemini via `google-genai`

## Project Structure

```text
app/
  main.py
  settings.py
  services/
    ai.py
    directions.py
  static/
    app.js
    styles.css
  templates/
    index.html
tests/
requirements.txt
```

## Requirements

- Python 3.11+
- Google Maps API key
- Gemini API key if using Gemini mode

## Environment Variables

Create a `.env` file in the project root:

```env
GOOGLE_MAPS_API_KEY=your_google_maps_key
AI_MODE=gemini
GEMINI_API_KEY=your_gemini_key
GEMINI_MODEL=gemini-2.5-flash
```

If you want to run without Gemini, use:

```env
AI_MODE=mock
```

## Install

```powershell
pip install -r requirements.txt
```

## Run

```powershell
uvicorn app.main:app --reload
```

Open:

`http://127.0.0.1:8000`

## Tests

```powershell
$env:PYTHONPATH='.'; python -m unittest discover -s tests -p "test_*.py" -v
```

## Notes

- Do not upload `.env` to GitHub.
- The app depends on Google Maps services for routes, Street View, and nearby places.
- Gemini mode depends on a valid `GEMINI_API_KEY`.

