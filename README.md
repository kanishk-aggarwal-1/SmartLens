# SmartLens

SmartLens is a browser-based smart-glasses demo built with FastAPI. It combines Google Maps walking directions, dual Street View lenses, route playback, voice-enabled translation, context-aware Gemini chat, and a live GPS panel in a single one-page interface.

## Features

- Dual-lens Street View layout with split left/right view rendering
- Walking route loading from typed start and destination inputs
- Route playback over the decoded walking path
- Right-lens navigation HUD with:
  - turn queue
  - live instruction card
  - notification chip
  - time chip
- GPS map with:
  - live current-position marker
  - follow toggle
  - recenter button
- Shared input box for translation and AI chat
- Text and voice translation
- Text and voice AI chat
- Browser speech output for translated text and AI replies
- Gemini chat with live context:
  - current route state
  - current simulated position
  - reverse-geocoded address
  - nearby places
  - Street View visual context
  - local date/time
  - live weather
- Mock AI fallback mode for demo use without Gemini tokens
- Health and metrics endpoints
- Optional login/API-key protection with hashed secrets
- Signup/login flow with per-user encrypted Google Maps and Gemini keys
- PostgreSQL or SQLite account storage
- Optional Redis cache backend
- Prometheus-compatible metrics export
- Docker, CI, Vercel, and Playwright smoke-test scaffolding

## Tech Stack

- Backend:
  - FastAPI
  - Pydantic Settings
  - Jinja2
  - httpx
  - PostgreSQL in production, SQLite for local development
  - Redis cache backend, optional
- Frontend:
  - Vanilla JavaScript
  - HTML/CSS
- Maps and location:
  - Google Maps JavaScript API
  - Google Directions API
  - Google Street View
  - Google Places
- AI:
  - Gemini via `google-genai`
- Weather:
  - Open-Meteo
- Testing:
  - Python `unittest`
  - Playwright

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
- Node.js if you want to run Playwright locally

## Environment Variables

Copy [`.env.example`](C:\Users\Kanishk\Desktop\SmartLens\.env.example) to `.env` in the project root:

```env
GOOGLE_MAPS_API_KEY=your_google_maps_key
AI_MODE=gemini
GEMINI_API_KEY=your_gemini_key
GEMINI_MODEL=gemini-2.5-flash
HTTP_TIMEOUT_S=10
PROVIDER_RETRY_COUNT=2
ROUTE_CACHE_TTL_S=300
WEATHER_CACHE_TTL_S=120
STREET_VIEW_CACHE_TTL_S=300
REDIS_URL=
CACHE_BACKEND=auto
REQUIRE_AUTH=false
API_KEY_HASHES=
SESSION_SECRET_KEY=replace-with-random-32-byte-secret
SECRET_ENCRYPTION_KEY=generate-with-python-scripts-security_hash-py-encryption-key
SESSION_TTL_S=86400
LOGIN_RATE_LIMIT_PER_MINUTE=5
API_RATE_LIMIT_PER_MINUTE=120
MAX_REQUEST_BODY_BYTES=1048576
ALLOWED_HOSTS=*
CORS_ORIGINS=
SECURE_COOKIES=true
HSTS_MAX_AGE_S=31536000
DATABASE_URL=sqlite:///./smartlens.db
ALLOW_SIGNUPS=true
```

If you want to run without Gemini, use:

```env
AI_MODE=mock
```

## App Modes

- `AI_MODE=gemini`
  - Uses Gemini for chat and translation
  - Requires `GEMINI_API_KEY`
- `AI_MODE=mock`
  - Uses mock chat and translation responses
  - Useful for demoing the app without AI API usage

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

## Manual Test Flow

After the app is running:

1. Enter a start location and destination.
2. Click `Load Route`.
3. Confirm the route loads, the right-lens turn queue updates, and Street View appears.
4. Click `Start Demo`.
5. Confirm the GPS marker moves, the route animates, and the right-lens instruction updates.
6. Drag the map and confirm follow mode stops holding center.
7. Click `Recenter` and confirm the map snaps back to the current route position.
8. Enter text in `Translate or ask AI` and test:
   - `Translate`
   - `Ask AI`
   - `Voice Translate`
   - `Voice Chat`
   - `Speak Translation`
   - `Speak AI`
9. Try questions such as:
   - `What time is it?`
   - `What is the weather here?`
   - `What places are nearby?`
   - `What am I looking at?`

## Health And Metrics

- Health: `http://127.0.0.1:8000/health`
- Metrics: `http://127.0.0.1:8000/metrics`
- Prometheus metrics: `http://127.0.0.1:8000/metrics/prometheus`

These endpoints expose runtime readiness, request latency summaries, provider status, and cache stats.

When `REQUIRE_AUTH=true`, metrics and API endpoints require either a signed login cookie or an API key sent as `X-API-Key` or `Authorization: Bearer ...`.

## Security

Authentication is off by default for local demo use. For production, users can create an account with an email, password, Google Maps API key, and optional Gemini API key.

Passwords are stored as PBKDF2-SHA256 hashes. Provider keys must be recoverable so the app can call Google Maps and Gemini, so they are encrypted at rest with `SECRET_ENCRYPTION_KEY`.

1. Generate the encryption key:

```powershell
$env:PYTHONPATH='.'; python scripts/security_hash.py --encryption-key
```

2. Optional: generate an API key hash for machine access to protected metrics/API routes:

```powershell
$env:PYTHONPATH='.'; python scripts/security_hash.py
```

3. Set these production environment variables:

```env
REQUIRE_AUTH=true
ALLOW_SIGNUPS=true
DATABASE_URL=postgresql://...
SECRET_ENCRYPTION_KEY=<generated encryption key>
SESSION_SECRET_KEY=<long random secret>
API_KEY_HASHES=<optional comma-separated API key hashes>
SECURE_COOKIES=true
ALLOWED_HOSTS=your-domain.vercel.app,your-custom-domain.com
CORS_ORIGINS=https://your-custom-domain.com
```

The app sets signed HttpOnly session cookies, rate-limits login/signup and API calls, applies common hardening headers, caps request body size, and can restrict hosts/CORS origins.

## Account Storage

Local development defaults to SQLite at `sqlite:///./smartlens.db`. Vercel production should use a hosted PostgreSQL database and set `DATABASE_URL` to its connection string.

Signup creates the account and immediately stores the user's Google Maps API key and optional Gemini key encrypted in the users table. After login, the app renders Google Maps with that user's key and uses that same user's Gemini key for AI calls.

## Redis Cache

Set `REDIS_URL` to enable Redis-backed TTL caches for routes, weather, and Street View context. Leave `CACHE_BACKEND=auto` to fall back to memory when Redis is not configured, or set `CACHE_BACKEND=redis` to require Redis in production.

## Docker

Build:

```powershell
docker build -t smartlens .
```

Run:

```powershell
docker run --rm -p 8000:8000 --env-file .env smartlens
```

## Tests

```powershell
$env:PYTHONPATH='.'; python -m unittest discover -s tests -p "test_*.py" -v
```

Current expected result:

- `20 tests OK`

## Browser E2E

Install Playwright dependencies:

```powershell
npm install
npx playwright install chromium
```

Run the browser smoke test:

```powershell
npx playwright test
```

The E2E flow uses `/?mockMaps=1` so it can exercise the UI without live Google Maps network calls.

## CI

GitHub Actions runs:

- Python unit and contract tests
- Playwright smoke tests against the mock-maps path

## Vercel Deployment

This repo includes:

- [vercel.json](C:\Users\Kanishk\Desktop\SmartLens\vercel.json) for the Python runtime entry point
- [api/index.py](C:\Users\Kanishk\Desktop\SmartLens\api\index.py) to expose the FastAPI app to Vercel
- [.github/workflows/deploy-vercel.yml](C:\Users\Kanishk\Desktop\SmartLens\.github\workflows\deploy-vercel.yml) to deploy from GitHub Actions

Configure these GitHub repository secrets:

```text
VERCEL_TOKEN
VERCEL_ORG_ID
VERCEL_PROJECT_ID
```

Configure the app environment variables in Vercel, including `DATABASE_URL`, `SECRET_ENCRYPTION_KEY`, `SESSION_SECRET_KEY`, `REDIS_URL` if using Redis, and the production security variables listed above. Global `GOOGLE_MAPS_API_KEY` and `GEMINI_API_KEY` can remain as fallback values, but signed-in users provide their own keys during signup.

## Current Limitations

- The app uses simulated route playback, not real device GPS.
- The lenses use Google Street View, not a live camera feed.
- Metrics are in-memory; caching is in-memory by default, with an optional Redis backend.
- Browser voice recognition quality depends on the browser and OS speech engine.
- Waypoints, alternate routes, and true AR road anchoring are not implemented.

## Notes

- Do not upload `.env` to GitHub.
- The app depends on Google Maps services for routes, Street View, and nearby places.
- Gemini mode depends on a valid `GEMINI_API_KEY`.
- Provider calls use small retry/backoff logic and in-memory TTL caches for route, weather, and Street View requests.
