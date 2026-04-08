let leftPano
let rightPano
let gpsMap
let gpsMarker
let routeLine
let streetViewService

const navState = {
  pathIndex: 0,
  segmentT: 0,
  currentStepIndex: 0,
  running: false,
  currentHeading: 0,
  walkSpeed: 0.003,
  routeLoaded: false,
  etaMinutes: null,
  remainingMeters: null,
  rerouteCooldownUntil: 0,
  routeLoading: false,
  panoLookupPending: false,
  lastPanoLookupAt: 0
}

const ARROW_STRAIGHT = "\u2191"
const ARROW_LEFT = "\u2190"
const ARROW_RIGHT = "\u2192"
const ARROW_UTURN = "\u2935"

const STORAGE_SOURCE = "smartlens:last_source"
const STORAGE_DEST = "smartlens:last_dest"

let routeSteps = []
let routePoints = []
let speechStartTimer = null
let speechPrimed = false
let aiContextCache = null
let lastPanoRefreshPos = null

const INITIAL_VIEW_POSITION = { lat: 0, lng: 0 }

let startLocation = null
let destination = null

const STEP_SWITCH_MIN_DISTANCE = 6
const ARRIVAL_DISTANCE = 4
const REROUTE_DEVIATION_METERS = 30
const REROUTE_COOLDOWN_MS = 10000
const PANO_LOOKUP_INTERVAL_MS = 700
const PANO_REFRESH_DISTANCE_M = 10
const SPEECH_START_DELAY_MS = 160
const SPEECH_PRIMER_DELAY_MS = 180
const SPEECH_LEAD_IN = ", "
const AI_CONTEXT_CACHE_MS = 15000
const AI_LOCATION_CACHE_MS = 60000

function ui(id) {
  return document.getElementById(id)
}

function notificationTextForStatus(message, tone) {
  if (tone === "error") return message
  if (tone === "pending") return message

  const normalized = (message || "").trim()
  const passiveMessages = new Set([
    "Demo running.",
    "Demo paused.",
    "Reset complete.",
    "Route loaded.",
    "Assistant response updated.",
    "Translation updated.",
    "Enter a start and destination, then load a route."
  ])

  if (
    passiveMessages.has(normalized) ||
    normalized.startsWith("Translation updated.")
  ) {
    return "No new notifications"
  }

  return normalized || "No new notifications"
}

function setInlineStatus(message, tone = "ok") {
  const el = ui("inlineStatus")
  el.textContent = message
  el.className = `inlineStatus ${tone}`
  if (ui("notificationText")) {
    ui("notificationText").textContent = notificationTextForStatus(message, tone)
  }
}

function setLensReadyState(ready) {
  ui("leftLens").classList.toggle("empty", !ready)
  ui("rightLens").classList.toggle("empty", !ready)
}

function setButtonLoading(buttonId, loading, labelWhenLoading) {
  const btn = ui(buttonId)
  if (!btn) return

  if (loading) {
    if (!btn.dataset.originalLabel) {
      btn.dataset.originalLabel = btn.textContent
    }
    btn.textContent = labelWhenLoading
  } else if (btn.dataset.originalLabel) {
    btn.textContent = btn.dataset.originalLabel
  }

  btn.disabled = loading
}

function getSpeechRecognitionCtor() {
  return window.SpeechRecognition || window.webkitSpeechRecognition || null
}

function supportsVoiceInput() {
  return Boolean(getSpeechRecognitionCtor())
}

function supportsVoiceOutput() {
  return Boolean(window.speechSynthesis)
}

function primeSpeechOutput() {
  if (!supportsVoiceOutput()) return
  window.speechSynthesis.getVoices()
}

function listenForSpeech(targetInputId, onDone, preferredLang = "auto") {
  const Recognition = getSpeechRecognitionCtor()
  if (!Recognition) {
    setInlineStatus("Voice input is not supported in this browser.", "error")
    return
  }

  const recognition = new Recognition()
  if (preferredLang !== "auto") {
    recognition.lang = preferredLang
  }
  recognition.interimResults = false
  recognition.maxAlternatives = 1

  const statusLang = preferredLang === "auto" ? "auto" : preferredLang
  setInlineStatus(`Listening (${statusLang})...`, "pending")

  recognition.onresult = (event) => {
    const transcript = event.results?.[0]?.[0]?.transcript || ""
    ui(targetInputId).value = transcript.trim()
    setInlineStatus("Voice captured.", "ok")
    if (onDone) onDone()
  }

  recognition.onerror = () => {
    setInlineStatus("Voice input failed. Try again.", "error")
  }

  recognition.onend = () => {
    if (ui("inlineStatus").textContent === "Listening...") {
      setInlineStatus("Voice capture ended.", "ok")
    }
  }

  recognition.start()
}

function speakText(text) {
  if (!supportsVoiceOutput()) {
    setInlineStatus("Voice output is not supported in this browser.", "error")
    return
  }

  const clean = (text || "").trim()
  if (!clean || clean === "-") {
    setInlineStatus("Nothing to speak yet.", "error")
    return
  }

  if (speechStartTimer) {
    clearTimeout(speechStartTimer)
    speechStartTimer = null
  }

  window.speechSynthesis.cancel()
  setInlineStatus("Preparing speech...", "pending")

  speechStartTimer = window.setTimeout(() => {
    const speakNow = () => {
      const utterance = new SpeechSynthesisUtterance(`${SPEECH_LEAD_IN}${clean}`)
      utterance.rate = 1
      utterance.pitch = 1
      utterance.onstart = () => setInlineStatus("Speaking...", "pending")
      utterance.onend = () => setInlineStatus("Speech finished.", "ok")
      utterance.onerror = () => setInlineStatus("Speech output failed.", "error")
      window.speechSynthesis.speak(utterance)
    }

    if (!speechPrimed) {
      const primer = new SpeechSynthesisUtterance(" ")
      primer.volume = 0
      primer.rate = 1
      primer.pitch = 1
      primer.onend = () => {
        speechPrimed = true
        window.setTimeout(speakNow, SPEECH_PRIMER_DELAY_MS)
      }
      primer.onerror = () => {
        speechPrimed = true
        speakNow()
      }
      window.speechSynthesis.speak(primer)
      speechStartTimer = null
      return
    }

    speakNow()
    speechStartTimer = null
  }, SPEECH_START_DELAY_MS)
}

function syncButtonStates() {
  ui("routeBtn").disabled = navState.routeLoading
  ui("startDemoBtn").disabled = navState.routeLoading || navState.running
  ui("stopDemoBtn").disabled = navState.routeLoading || !navState.running
  ui("resetDemoBtn").disabled = navState.routeLoading
}

function interpolate(a, b, t) {
  return {
    lat: a.lat + (b.lat - a.lat) * t,
    lng: a.lng + (b.lng - a.lng) * t
  }
}

function calculateHeading(a, b) {
  const lat1 = a.lat * Math.PI / 180
  const lat2 = b.lat * Math.PI / 180
  const dLon = (b.lng - a.lng) * Math.PI / 180

  const y = Math.sin(dLon) * Math.cos(lat2)
  const x =
    Math.cos(lat1) * Math.sin(lat2) -
    Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon)

  let brng = Math.atan2(y, x)
  brng = brng * 180 / Math.PI
  brng = (brng + 360) % 360
  return brng
}

function distanceMeters(a, b) {
  const R = 6371000
  const lat1 = a.lat * Math.PI / 180
  const lat2 = b.lat * Math.PI / 180

  const dLat = (b.lat - a.lat) * Math.PI / 180
  const dLng = (b.lng - a.lng) * Math.PI / 180

  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) *
    Math.sin(dLng / 2) ** 2

  const c = 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h))
  return R * c
}

function arrowFromManeuver(maneuver) {
  if (!maneuver) return ARROW_STRAIGHT
  const m = maneuver.toLowerCase()
  if (m.includes("left")) return ARROW_LEFT
  if (m.includes("right")) return ARROW_RIGHT
  if (m.includes("uturn")) return ARROW_UTURN
  return ARROW_STRAIGHT
}

function stripHTML(html) {
  const div = document.createElement("div")
  div.innerHTML = html
  return div.textContent || div.innerText || ""
}

function currentPositionForContext() {
  if (gpsMarker) {
    const markerPos = gpsMarker.getPosition()
    if (markerPos) {
      return { lat: markerPos.lat(), lng: markerPos.lng() }
    }
  }

  if (routePoints.length > 0) {
    const index = Math.min(navState.pathIndex, routePoints.length - 1)
    return routePoints[index]
  }

  return null
}

function cacheKeyForPosition(pos) {
  if (!pos) return "none"
  return `${pos.lat.toFixed(4)},${pos.lng.toFixed(4)}`
}

function classifyChatNeeds(message) {
  const text = (message || "").toLowerCase()
  const visual = /(what (am i|do you) (seeing|see)|what(?:'s| is) (this|that|in front)|building|store|shop|sign|place|landmark|restaurant|cafe|look at|visible)/.test(text)
  const weather = /(weather|temperature|rain|raining|wind|humid|humidity|forecast|sunny|cloudy|snow)/.test(text)
  const nearby = visual || /(nearby|around me|close by|nearest|near me|restaurant|cafe|shop|store|pharmacy|gas station)/.test(text)
  const time = /(what time|current time|date today|today'?s date|what day)/.test(text)

  return {
    needsPlaces: nearby,
    needsStreetView: visual,
    needsWeather: weather,
    needsAddress: nearby || visual || weather,
    isSimpleTime: time && !visual && !weather && !nearby
  }
}

function reverseGeocodePosition(pos) {
  if (!pos) return Promise.resolve(null)

  const geocoder = new google.maps.Geocoder()
  return new Promise((resolve) => {
    geocoder.geocode({ location: pos }, (results, status) => {
      if (status === "OK" && results && results[0]) {
        resolve(results[0].formatted_address)
        return
      }
      resolve(null)
    })
  })
}

function fetchNearbyPlaces(pos) {
  if (!pos || !google.maps.places) return Promise.resolve([])

  const service = new google.maps.places.PlacesService(gpsMap)
  return new Promise((resolve) => {
    service.nearbySearch(
      {
        location: pos,
        radius: 120
      },
      (results, status) => {
        if (status !== google.maps.places.PlacesServiceStatus.OK || !results) {
          resolve([])
          return
        }

        resolve(results.slice(0, 5).map((place) => ({
          name: place.name || "",
          vicinity: place.vicinity || "",
          types: (place.types || []).slice(0, 3),
          rating: place.rating || null
        })))
      }
    )
  })
}

async function buildAiContext(message = "") {
  const pos = currentPositionForContext()
  const cacheKey = cacheKeyForPosition(pos)
  const needs = classifyChatNeeds(message)
  const now = Date.now()

  if (
    aiContextCache &&
    aiContextCache.key === cacheKey &&
    now - aiContextCache.createdAt < AI_LOCATION_CACHE_MS
  ) {
    return {
      ...getRouteContext(),
      ...aiContextCache.payload,
      requested_context: needs
    }
  }

  const payload = {
    current_position: pos ? {
      lat: Number(pos.lat.toFixed(6)),
      lng: Number(pos.lng.toFixed(6))
    } : null,
    street_view: {
      heading: Number(navState.currentHeading.toFixed(1)),
      pitch: 5,
      fov: 90,
      pano_id: leftPano ? leftPano.getPano() : null
    },
    client_time: new Date().toISOString()
  }

  const work = []
  if (needs.needsAddress) {
    work.push(reverseGeocodePosition(pos).then((value) => {
      payload.current_address = value
    }))
  }
  if (needs.needsPlaces) {
    work.push(fetchNearbyPlaces(pos).then((value) => {
      payload.nearby_places = value
    }))
  }

  if (work.length) {
    await Promise.all(work)
  }

  aiContextCache = {
    key: cacheKey,
    createdAt: now,
    payload
  }

  return {
    ...getRouteContext(),
    ...payload,
    requested_context: needs
  }
}

function setNavMessage(primary, secondary = "") {
  ui("navPrimary").textContent = primary
}

function setNextPreview(currentDistance = null) {
  const queueSteps = routeSteps
    .slice(navState.currentStepIndex, navState.currentStepIndex + 3)
    .map((step, index) => ({
      text: stripHTML(step.instruction_html || "Continue"),
      distance: index === 0 && currentDistance !== null
        ? `${Math.max(0, Math.round(currentDistance))} m`
        : (step.distance_m ? `${Math.max(0, Math.round(step.distance_m))} m` : "-"),
      arrow: arrowFromManeuver(step.maneuver)
    }))
    .filter((step) => step.text)

  if (!queueSteps.length) {
    ui("navQueue").textContent = "-"
    return
  }

  ui("navQueue").innerHTML = queueSteps.map((step, index) =>
    `<div class="turnQueueItem ${index === 0 ? "current" : "queued"}">` +
    `<span class="turnQueueArrow">${step.arrow}</span>` +
    `<span class="turnQueueDistance">${step.distance}</span>` +
    `</div>`
  ).join("")
}

function updateRemainingStats(pos) {
  if (navState.pathIndex >= routePoints.length - 1) {
    navState.remainingMeters = 0
    navState.etaMinutes = 0
    return
  }

  let total = 0
  const current = routePoints[navState.pathIndex]
  const next = routePoints[navState.pathIndex + 1]
  if (current && next) {
    total += distanceMeters(pos, next)
  }

  for (let i = navState.pathIndex + 1; i < routePoints.length - 1; i++) {
    total += distanceMeters(routePoints[i], routePoints[i + 1])
  }

  navState.remainingMeters = Math.max(0, Math.floor(total))
  const metersPerSecond = Math.max(0.5, navState.walkSpeed * 110000)
  navState.etaMinutes = Math.max(1, Math.round((total / metersPerSecond) / 60))
}

function maybeSwitchStep(pos) {
  const step = routeSteps[navState.currentStepIndex]
  if (!step || navState.currentStepIndex >= routeSteps.length - 1) return

  const nextStep = routeSteps[navState.currentStepIndex + 1]
  if (!nextStep || !step.end_location || !nextStep.end_location) return

  const end = step.end_location
  const nextEnd = nextStep.end_location

  const distCurrent = distanceMeters(pos, { lat: end.lat, lng: end.lng })
  const distNext = distanceMeters(pos, { lat: nextEnd.lat, lng: nextEnd.lng })

  const dynamicThreshold = Math.max(STEP_SWITCH_MIN_DISTANCE, Math.min(18, (step.distance_m || 40) * 0.2))

  if (distCurrent <= dynamicThreshold || distNext + 3 < distCurrent) {
    navState.currentStepIndex++
  }
}

function updateNavigation(pos) {
  const step = routeSteps[navState.currentStepIndex]
  if (!step) return

  maybeSwitchStep(pos)

  const currentStep = routeSteps[navState.currentStepIndex]
  const end = currentStep.end_location
  const dist = end ? Math.floor(distanceMeters(pos, { lat: end.lat, lng: end.lng })) : 0

  setNavMessage(stripHTML(currentStep.instruction_html || "Continue"))

  setNextPreview(dist)

  if (navState.currentStepIndex === routeSteps.length - 1 && dist < ARRIVAL_DISTANCE) {
    setNavMessage("You have arrived")
    setNextPreview(0)
    navState.running = false
    syncButtonStates()
  }
}

function nearestDistanceToRoute(pos) {
  if (routePoints.length === 0) return 0
  const from = Math.max(0, navState.pathIndex - 6)
  const to = Math.min(routePoints.length - 1, navState.pathIndex + 12)
  let min = Number.POSITIVE_INFINITY

  for (let i = from; i <= to; i++) {
    const d = distanceMeters(pos, routePoints[i])
    if (d < min) min = d
  }

  return min
}

async function fetchRoute(origin, dest) {
  const r = await fetch("/api/route", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ origin: origin, destination: dest })
  })

  if (!r.ok) {
    throw new Error("Route request failed.")
  }

  const data = await r.json()

  if (data.status !== "OK") {
    throw new Error(data.error_message || (`Route service status: ${data.status}`))
  }

  if (!data.overview_polyline) {
    throw new Error("No route polyline returned.")
  }

  const detailedSegments = data.detailed_path || []
  const detailedPoints = []

  for (const segment of detailedSegments) {
    const decodedSegment = google.maps.geometry.encoding.decodePath(segment)
    for (const point of decodedSegment) {
      detailedPoints.push({ lat: point.lat(), lng: point.lng() })
    }
  }

  if (detailedPoints.length > 1) {
    routePoints = detailedPoints
  } else {
    const decoded = google.maps.geometry.encoding.decodePath(data.overview_polyline)
    routePoints = decoded.map((p) => ({ lat: p.lat(), lng: p.lng() }))
  }
  routeSteps = data.steps || []

  navState.currentStepIndex = 0
  navState.pathIndex = 0
  navState.segmentT = 0
  navState.routeLoaded = true
  if (routePoints.length > 0) {
    startLocation = { ...routePoints[0] }
    destination = { ...routePoints[routePoints.length - 1] }
  }

  routeLine.setPath(routePoints)
  if (routePoints[0] && gpsMarker && gpsMap) {
    ensurePanoramas(startLocation)
    setLensReadyState(true)
    leftPano.setPosition(startLocation)
    rightPano.setPosition(startLocation)
    gpsMarker.setPosition(startLocation)
    gpsMap.setCenter(startLocation)
    gpsMap.setZoom(17)
    syncLensPanoramas(routePoints[0], navState.currentHeading)
    requestPanoramaRefresh(routePoints[0], navState.currentHeading)
  }

  if (data.total_duration_s) {
    navState.etaMinutes = Math.max(1, Math.round(data.total_duration_s / 60))
  }
  if (data.total_distance_m) {
    navState.remainingMeters = data.total_distance_m
  }
}

function updateLensPOV(heading) {
  if (!leftPano || !rightPano) return
  leftPano.setPov({ heading, pitch: 5 })
  rightPano.setPov({ heading, pitch: 5 })
}

function ensurePanoramas(position) {
  if (leftPano && rightPano) return

  leftPano = new google.maps.StreetViewPanorama(ui("leftPano"), {
    position,
    pov: { heading: 0, pitch: 5 },
    disableDefaultUI: true,
    linksControl: false,
    panControl: false,
    enableCloseButton: false,
    addressControl: false,
    fullscreenControl: false,
    motionTracking: false,
    clickToGo: false,
    scrollwheel: false,
    disableDoubleClickZoom: true
  })

  rightPano = new google.maps.StreetViewPanorama(ui("rightPano"), {
    position,
    pov: { heading: 0, pitch: 5 },
    disableDefaultUI: true,
    linksControl: false,
    panControl: false,
    enableCloseButton: false,
    addressControl: false,
    fullscreenControl: false,
    motionTracking: false,
    clickToGo: false,
    scrollwheel: false,
    disableDoubleClickZoom: true
  })
}

function snapPanorama(panorama, pos, heading) {
  return new Promise((resolve) => {
    if (!streetViewService) {
      panorama.setPosition(pos)
      panorama.setPov({ heading, pitch: 5 })
      resolve()
      return
    }

    streetViewService.getPanorama(
      {
        location: pos,
        radius: 40,
        source: google.maps.StreetViewSource.OUTDOOR,
        preference: google.maps.StreetViewPreference.NEAREST
      },
      (data, status) => {
        if (status === "OK" && data && data.location && data.location.pano) {
          panorama.setPano(data.location.pano)
          panorama.setPosition(data.location.latLng || pos)
        } else {
          panorama.setPosition(pos)
        }

        panorama.setPov({ heading, pitch: 5 })
        resolve()
      }
    )
  })
}

function requestPanoramaRefresh(pos, heading) {
  if (!leftPano || !rightPano || navState.panoLookupPending) return

  const now = Date.now()
  if (now - navState.lastPanoLookupAt < PANO_LOOKUP_INTERVAL_MS) {
    return
  }
  if (lastPanoRefreshPos && distanceMeters(lastPanoRefreshPos, pos) < PANO_REFRESH_DISTANCE_M) {
    return
  }

  navState.panoLookupPending = true
  navState.lastPanoLookupAt = now
  lastPanoRefreshPos = { ...pos }

  Promise.all([
    snapPanorama(leftPano, pos, heading),
    snapPanorama(rightPano, pos, heading)
  ]).finally(() => {
    navState.panoLookupPending = false
  })
}

function syncLensPanoramas(pos, heading) {
  if (!leftPano || !rightPano) return

  updateLensPOV(heading)
  requestPanoramaRefresh(pos, heading)
}

async function loadRoute(origin, dest, opts = {}) {
  navState.routeLoading = true
  setButtonLoading("routeBtn", true, "Loading...")
  setButtonLoading("startDemoBtn", true, "Preparing...")
  syncButtonStates()

  try {
    await fetchRoute(origin, dest)
    setInlineStatus("Route loaded.", "ok")
    setNavMessage("Route ready", `${navState.remainingMeters || 0} m`)
    setNextPreview()
  } catch (err) {
    navState.routeLoaded = false
    setInlineStatus(err.message, "error")
    setNavMessage("Route unavailable", "Fix inputs and load again")
    throw err
  } finally {
    navState.routeLoading = false
    setButtonLoading("routeBtn", false)
    setButtonLoading("startDemoBtn", false)
    syncButtonStates()
  }
}

function geocodeAddress(geocoder, address) {
  return new Promise((resolve, reject) => {
    geocoder.geocode({ address }, (results, status) => {
      if (status !== "OK" || !results || !results[0]) {
        reject(new Error(`Address not found: ${address}`))
        return
      }
      resolve(results[0].geometry.location)
    })
  })
}

async function resolveInputsToCoordinates() {
  const source = ui("sourceInput").value.trim()
  const dest = ui("destInput").value.trim()

  if (!source || !dest) {
    throw new Error("Enter both start and destination.")
  }

  const geocoder = new google.maps.Geocoder()
  const s = await geocodeAddress(geocoder, source)
  const d = await geocodeAddress(geocoder, dest)

  startLocation = { lat: s.lat(), lng: s.lng() }
  destination = { lat: d.lat(), lng: d.lng() }

  localStorage.setItem(STORAGE_SOURCE, source)
  localStorage.setItem(STORAGE_DEST, dest)

  return {
    origin: startLocation,
    destination: destination
  }
}

async function loadRouteFromInputs() {
  try {
    setInlineStatus("Resolving addresses...", "pending")
    const payload = await resolveInputsToCoordinates()
    await loadRoute(payload.origin, payload.destination)
  } catch (err) {
    setInlineStatus(err.message, "error")
  }
}

async function rerouteFromPosition(pos) {
  const now = Date.now()
  if (now < navState.rerouteCooldownUntil || navState.routeLoading) return
  navState.rerouteCooldownUntil = now + REROUTE_COOLDOWN_MS

  try {
    setInlineStatus("Off-route detected. Re-routing...", "pending")
    await loadRoute(pos, destination, { silent: true })
  } catch (_) {
    setInlineStatus("Re-route failed. Try Retry Route.", "error")
  }
}

function animate() {
  if (!navState.running) return
  if (routePoints.length === 0) return

  const start = routePoints[navState.pathIndex]
  const end = routePoints[navState.pathIndex + 1]

  if (!start || !end) {
    navState.running = false
    setNavMessage("Route complete")
    setNextPreview()
    setInlineStatus("Demo complete.", "ok")
    syncButtonStates()
    return
  }

  navState.segmentT += navState.walkSpeed

  if (navState.segmentT >= 1) {
    navState.segmentT = 0
    navState.pathIndex++
  }

  const pos = interpolate(start, end, navState.segmentT)
  const targetHeading = calculateHeading(pos, end)

  navState.currentHeading =
    (navState.currentHeading * 0.85) +
    (targetHeading * 0.15)

  syncLensPanoramas(pos, navState.currentHeading)

  gpsMarker.setPosition(pos)
  gpsMap.setCenter(pos)

  ui("latText").textContent = pos.lat.toFixed(6)
  ui("lngText").textContent = pos.lng.toFixed(6)

  updateRemainingStats(pos)
  updateNavigation(pos)

  const offRouteBy = nearestDistanceToRoute(pos)
  if (offRouteBy > REROUTE_DEVIATION_METERS) {
    rerouteFromPosition(pos)
  }

  requestAnimationFrame(() => {
    animate()
  })
}

async function startDemo() {
  if (navState.running) return

  try {
    if (!navState.routeLoaded || routePoints.length === 0) {
      setInlineStatus("No route loaded. Loading now...", "pending")
      const payload = await resolveInputsToCoordinates()
      await loadRoute(payload.origin, payload.destination)
    }

    navState.running = true
    setInlineStatus("Demo running.", "ok")
    syncButtonStates()
    animate()
  } catch (err) {
    navState.running = false
    setInlineStatus(err.message, "error")
    syncButtonStates()
  }
}

function stopDemo() {
  navState.running = false
  setNavMessage("Paused", "Press Start Demo")
  setInlineStatus("Demo paused.", "ok")
  syncButtonStates()
}

function resetDemo() {
  navState.running = false
  navState.pathIndex = 0
  navState.segmentT = 0
  navState.currentStepIndex = 0
  navState.currentHeading = 0
  navState.routeLoaded = false
  navState.etaMinutes = null
  navState.remainingMeters = null
  navState.panoLookupPending = false
  navState.lastPanoLookupAt = 0
  lastPanoRefreshPos = null

  routeSteps = []
  routePoints = []

  if (routeLine) routeLine.setPath([])

  if (leftPano && rightPano) {
    ui("leftPano").innerHTML = ""
    ui("rightPano").innerHTML = ""
    leftPano = null
    rightPano = null
  }

  if (gpsMarker && gpsMap) {
    gpsMarker.setPosition(INITIAL_VIEW_POSITION)
    gpsMap.setCenter(INITIAL_VIEW_POSITION)
    gpsMap.setZoom(2)
  }

  startLocation = null
  destination = null
  aiContextCache = null
  setLensReadyState(false)

  setNavMessage("Waiting for route...", "-")
  setNextPreview()
  setInlineStatus("Reset complete.", "ok")
  syncButtonStates()
}

function getRouteContext() {
  return {
    route_loaded: navState.routeLoaded,
    running: navState.running,
    nav_primary: ui("navPrimary") ? ui("navPrimary").textContent : "",
    nav_secondary: "",
    eta_min: navState.etaMinutes,
    remaining_m: navState.remainingMeters
  }
}

async function runTranslate() {
  const text = ui("promptInput").value.trim()
  const sourceLang = ui("sourceLangInput").value
  const targetLang = ui("targetLangInput").value

  if (!text) {
    setInlineStatus("Enter text for translation.", "error")
    return
  }

  const r = await fetch("/api/translate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, source_lang: sourceLang, target_lang: targetLang })
  })

  if (!r.ok) {
    let message = "Translation request failed."
    try {
      const err = await r.json()
      message = err.detail || message
    } catch (_) {
      // keep default
    }
    setInlineStatus(message, "error")
    return
  }

  const data = await r.json()
  ui("translationText").textContent = data.translated_text || "-"
  const detected = data.detected_source_lang ? ` Detected: ${data.detected_source_lang}.` : ""
  setInlineStatus(`Translation updated.${detected}`, "ok")
}

async function runChat(messageOverride = null, intent = null) {
  const message = (messageOverride || ui("promptInput").value || "").trim()
  if (!message) {
    setInlineStatus("Enter a chat prompt.", "error")
    return
  }

  let chatContext
  try {
    const needs = classifyChatNeeds(message)
    setInlineStatus(needs.isSimpleTime ? "Preparing reply..." : "Gathering live context...", "pending")
    chatContext = await buildAiContext(message)
  } catch (err) {
    setInlineStatus(`Context error: ${err.message || "Unable to gather live context."}`, "error")
    return
  }

  const r = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      intent,
      context: chatContext
    })
  })

  if (!r.ok) {
    let message = "Chat request failed."
    try {
      const err = await r.json()
      message = err.detail || message
    } catch (_) {
      // keep default
    }
    setInlineStatus(message, "error")
    return
  }

  const data = await r.json()
  ui("aiText").textContent = data.reply || "-"
  setInlineStatus("Assistant response updated.", "ok")
}

function initMaps() {
  gpsMap = new google.maps.Map(ui("gpsMap"), {
    center: INITIAL_VIEW_POSITION,
    zoom: 2,
    disableDefaultUI: false,
    streetViewControl: false,
    mapTypeControl: false,
    fullscreenControl: false,
    zoomControl: true,
    gestureHandling: "greedy",
    draggable: true,
    scrollwheel: true
  })

  gpsMarker = new google.maps.Marker({
    position: INITIAL_VIEW_POSITION,
    map: gpsMap,
    zIndex: 1000,
    icon: {
      path: google.maps.SymbolPath.CIRCLE,
      scale: 9,
      fillColor: "#6be3ff",
      fillOpacity: 1,
      strokeColor: "#ffffff",
      strokeOpacity: 0.95,
      strokeWeight: 3
    }
  })

  routeLine = new google.maps.Polyline({
    map: gpsMap,
    strokeColor: "#6be3ff",
    strokeWeight: 5
  })
  streetViewService = new google.maps.StreetViewService()
  setLensReadyState(false)

  setNavMessage("Load a route to begin", "-")
  setNextPreview()
}

function initControls() {
  ui("routeBtn").onclick = loadRouteFromInputs
  ui("startDemoBtn").onclick = startDemo
  ui("stopDemoBtn").onclick = stopDemo
  ui("resetDemoBtn").onclick = resetDemo

  ui("translateBtn").onclick = () => runTranslate()
  ui("translateVoiceBtn").onclick = () => {
    listenForSpeech("promptInput", () => runTranslate(), ui("sourceLangInput").value)
  }
  ui("chatBtn").onclick = () => runChat()
  ui("chatVoiceBtn").onclick = () => {
    listenForSpeech("promptInput", () => runChat())
  }
  ui("speakAiBtn").onclick = () => {
    speakText(ui("aiText").textContent)
  }
  ui("speakTranslateBtn").onclick = () => {
    speakText(ui("translationText").textContent)
  }

  const savedSource = localStorage.getItem(STORAGE_SOURCE)
  const savedDest = localStorage.getItem(STORAGE_DEST)
  if (savedSource) ui("sourceInput").value = savedSource
  if (savedDest) ui("destInput").value = savedDest

  if (!supportsVoiceInput()) {
    ui("translateVoiceBtn").disabled = true
    ui("chatVoiceBtn").disabled = true
  }
  if (!supportsVoiceOutput()) {
    ui("speakAiBtn").disabled = true
    ui("speakTranslateBtn").disabled = true
  }
  syncButtonStates()
}

function startClock() {
  setInterval(() => {
    const now = new Date()
    ui("timeText").textContent = now.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit"
    })
  }, 1000)
}

window.addEventListener("load", () => {
  const waitGoogle = setInterval(() => {
    if (window.google && window.google.maps) {
      clearInterval(waitGoogle)
      initMaps()
      initControls()
      startClock()
      primeSpeechOutput()
      setInlineStatus("Enter a start and destination, then load a route.", "ok")
    }
  }, 50)
})
