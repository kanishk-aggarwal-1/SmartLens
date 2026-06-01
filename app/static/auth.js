const loginForm = document.getElementById("loginForm")
const signupForm = document.getElementById("signupForm")
const toggleAuthMode = document.getElementById("toggleAuthMode")
const authTitle = document.getElementById("authTitle")
const authStatus = document.getElementById("authStatus")

let signupMode = false

function setAuthStatus(message, tone = "ok") {
  authStatus.textContent = message
  authStatus.className = `inlineStatus ${tone}`
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })

  if (!response.ok) {
    let message = "Request failed."
    try {
      const body = await response.json()
      message = body.detail?.message || body.detail || message
    } catch (_) {
      // keep default
    }
    throw new Error(message)
  }

  return response.json()
}

function setSignupMode(enabled) {
  signupMode = enabled
  if (!signupForm || !toggleAuthMode) return
  loginForm.classList.toggle("hidden", enabled)
  signupForm.classList.toggle("hidden", !enabled)
  authTitle.textContent = enabled ? "Create account" : "Log in"
  toggleAuthMode.textContent = enabled ? "I already have an account" : "Create an account"
  setAuthStatus(
    enabled ? "Create an account and save your private provider keys." : "Use your account to load your private API keys.",
    "ok"
  )
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault()
  setAuthStatus("Logging in...", "pending")
  try {
    await postJson("/auth/login", {
      email: document.getElementById("loginEmail").value.trim(),
      password: document.getElementById("loginPassword").value
    })
    window.location.href = "/"
  } catch (error) {
    setAuthStatus(error.message, "error")
  }
})

if (signupForm) {
  signupForm.addEventListener("submit", async (event) => {
    event.preventDefault()
    setAuthStatus("Creating account...", "pending")
    try {
      await postJson("/auth/signup", {
        email: document.getElementById("signupEmail").value.trim(),
        password: document.getElementById("signupPassword").value,
        google_maps_api_key: document.getElementById("signupGoogleKey").value.trim(),
        gemini_api_key: document.getElementById("signupGeminiKey").value.trim() || null
      })
      window.location.href = "/"
    } catch (error) {
      setAuthStatus(error.message, "error")
    }
  })
}

if (toggleAuthMode) {
  toggleAuthMode.addEventListener("click", () => {
    setSignupMode(!signupMode)
  })
}
