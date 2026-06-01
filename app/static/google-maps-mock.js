(function () {
  class MockLatLng {
    constructor(lat, lng) {
      this._lat = lat
      this._lng = lng
    }
    lat() { return this._lat }
    lng() { return this._lng }
  }

  class MockMap {
    constructor(_el, options = {}) {
      this.center = options.center || { lat: 0, lng: 0 }
      this.zoom = options.zoom || 2
      this.listeners = {}
    }
    setCenter(center) { this.center = center }
    setZoom(zoom) { this.zoom = zoom }
    addListener(event, cb) {
      this.listeners[event] = cb
      return { remove() {} }
    }
  }

  class MockMarker {
    constructor(options = {}) {
      this.position = options.position || { lat: 0, lng: 0 }
    }
    setPosition(position) { this.position = position }
    getPosition() { return new MockLatLng(this.position.lat, this.position.lng) }
  }

  class MockPolyline {
    constructor(options = {}) {
      this.path = options.path || []
    }
    setPath(path) { this.path = path }
  }

  class MockStreetViewPanorama {
    constructor(_el, options = {}) {
      this.position = options.position || { lat: 0, lng: 0 }
      this.pov = options.pov || { heading: 0, pitch: 5 }
      this.pano = "mock-pano"
    }
    setPosition(position) { this.position = position }
    setPov(pov) { this.pov = pov }
    setPano(pano) { this.pano = pano }
    getPano() { return this.pano }
  }

  class MockStreetViewService {
    getPanorama(request, callback) {
      callback(
        {
          location: {
            pano: "mock-pano",
            latLng: new MockLatLng(request.location.lat, request.location.lng)
          }
        },
        "OK"
      )
    }
  }

  class MockGeocoder {
    geocode(request, callback) {
      if (request.location) {
        callback([{ formatted_address: "Mock Address" }], "OK")
        return
      }

      const text = String(request.address || "").toLowerCase()
      const base = text.includes("destination")
        ? { lat: 40.7592, lng: -73.9847 }
        : { lat: 40.758, lng: -73.9855 }

      callback(
        [{
          formatted_address: request.address,
          geometry: {
            location: new MockLatLng(base.lat, base.lng)
          }
        }],
        "OK"
      )
    }
  }

  class MockPlacesService {
    constructor(_map) {}
    nearbySearch(_request, callback) {
      callback(
        [
          { name: "Mock Cafe", vicinity: "Broadway", types: ["cafe"], rating: 4.4 },
          { name: "Mock Pharmacy", vicinity: "7th Ave", types: ["pharmacy"], rating: 4.1 }
        ],
        "OK"
      )
    }
  }

  const encodedPaths = {
    "seg-1": [
      { lat: 40.758, lng: -73.9855 },
      { lat: 40.7584, lng: -73.9852 },
      { lat: 40.7587, lng: -73.9850 }
    ],
    "seg-2": [
      { lat: 40.7587, lng: -73.9850 },
      { lat: 40.7590, lng: -73.9849 },
      { lat: 40.7592, lng: -73.9847 }
    ],
    "route-overview": [
      { lat: 40.758, lng: -73.9855 },
      { lat: 40.7585, lng: -73.9851 },
      { lat: 40.7592, lng: -73.9847 }
    ]
  }

  const maps = {
    Map: MockMap,
    Marker: MockMarker,
    Polyline: MockPolyline,
    StreetViewPanorama: MockStreetViewPanorama,
    StreetViewService: MockStreetViewService,
    Geocoder: MockGeocoder,
    SymbolPath: { CIRCLE: "CIRCLE" },
    StreetViewSource: { OUTDOOR: "OUTDOOR" },
    StreetViewPreference: { NEAREST: "NEAREST" },
    places: {
      PlacesService: MockPlacesService,
      PlacesServiceStatus: { OK: "OK" }
    },
    geometry: {
      encoding: {
        decodePath(encoded) {
          const points = encodedPaths[encoded] || encodedPaths["route-overview"]
          return points.map((point) => new MockLatLng(point.lat, point.lng))
        }
      }
    }
  }

  window.google = { maps }
})()
