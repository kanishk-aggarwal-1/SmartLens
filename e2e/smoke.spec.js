const { test, expect } = require("@playwright/test")

test("core flow works with mocked maps", async ({ page }) => {
  await page.route("**/api/route", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "OK",
        summary: "Mock route",
        total_distance_m: 240,
        total_duration_s: 180,
        overview_polyline: "route-overview",
        detailed_path: ["seg-1", "seg-2"],
        steps: [
          {
            instruction_html: "Head north on Broadway",
            distance_m: 120,
            duration_s: 90,
            maneuver: "straight",
            start_location: { lat: 40.758, lng: -73.9855 },
            end_location: { lat: 40.7587, lng: -73.9850 },
            polyline: "seg-1"
          },
          {
            instruction_html: "Turn right toward destination",
            distance_m: 120,
            duration_s: 90,
            maneuver: "turn-right",
            start_location: { lat: 40.7587, lng: -73.9850 },
            end_location: { lat: 40.7592, lng: -73.9847 },
            polyline: "seg-2"
          }
        ]
      })
    })
  })

  await page.route("**/api/translate", async (route) => {
    const body = route.request().postDataJSON()
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        mode: "mock",
        translated_text: `[${body.target_lang.toUpperCase()}] ${body.text}`,
        detected_source_lang: body.source_lang,
        target_lang: body.target_lang
      })
    })
  })

  await page.route("**/api/chat", async (route) => {
    const body = route.request().postDataJSON()
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        mode: "mock",
        reply: `Echo: ${body.message}`
      })
    })
  })

  await page.goto("/?mockMaps=1")
  await expect(page.locator("#inlineStatus")).toContainText("Enter a start and destination")

  await page.fill("#sourceInput", "Start location")
  await page.fill("#destInput", "Destination")
  await page.click("#routeBtn")
  await expect(page.locator("#inlineStatus")).toContainText("Route loaded")
  await expect(page.locator("#navPrimary")).toContainText("Route ready")

  await page.click("#startDemoBtn")
  await expect(page.locator("#inlineStatus")).toContainText("Demo running")

  await page.click("#stopDemoBtn")
  await expect(page.locator("#inlineStatus")).toContainText("Demo paused")

  await page.click("#followToggleBtn")
  await expect(page.locator("#followToggleBtn")).toContainText("Follow: Off")

  await page.click("#recenterBtn")
  await expect(page.locator("#followToggleBtn")).toContainText("Follow: On")

  await page.fill("#promptInput", "Where is the station?")
  await page.click("#translateBtn")
  await expect(page.locator("#translationText")).toContainText("[EN] Where is the station?")

  await page.fill("#promptInput", "What time is it?")
  await page.click("#chatBtn")
  await expect(page.locator("#aiText")).toContainText("Echo: What time is it?")

  await page.click("#resetDemoBtn")
  await expect(page.locator("#inlineStatus")).toContainText("Reset complete")
})
