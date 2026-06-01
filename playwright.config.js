const { defineConfig } = require("@playwright/test")

module.exports = defineConfig({
  testDir: "./e2e",
  timeout: 30000,
  use: {
    baseURL: "http://127.0.0.1:8000",
    headless: true
  },
  webServer: {
    command: "python -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
    url: "http://127.0.0.1:8000/health",
    reuseExistingServer: true,
    timeout: 120000
  }
})
