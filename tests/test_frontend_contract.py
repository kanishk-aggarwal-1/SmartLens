import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")


class FrontendContractTests(unittest.TestCase):
    def test_required_controls_exist(self):
        required_ids = [
            "routeBtn",
            "startDemoBtn",
            "stopDemoBtn",
            "resetDemoBtn",
            "promptInput",
            "sourceLangInput",
            "targetLangInput",
            "translateVoiceBtn",
            "chatVoiceBtn",
            "speakAiBtn",
            "speakTranslateBtn",
            "inlineStatus",
            "navQueue",
            "notificationText",
        ]
        for control_id in required_ids:
            self.assertIn(f'id="{control_id}"', HTML)

    def test_core_handlers_wired(self):
        expected_snippets = [
            'ui("routeBtn").onclick = loadRouteFromInputs',
            'ui("startDemoBtn").onclick = startDemo',
            'ui("stopDemoBtn").onclick = stopDemo',
            'ui("resetDemoBtn").onclick = resetDemo',
            'ui("translateVoiceBtn").onclick = () => {',
            'ui("chatVoiceBtn").onclick = () => {',
            'ui("speakAiBtn").onclick = () => {',
            'ui("speakTranslateBtn").onclick = () => {',
            'body: JSON.stringify({ text, source_lang: sourceLang, target_lang: targetLang })',
            'chatContext = await buildAiContext(message)',
            "function classifyChatNeeds(message) {",
            "function setNextPreview(currentDistance = null) {",
            "turnQueueItem",
        ]
        for snippet in expected_snippets:
            self.assertIn(snippet, JS)

    def test_start_stop_state_management_present(self):
        self.assertIn("syncButtonStates()", JS)
        self.assertIn("setInlineStatus(\"Demo running.\", \"ok\")", JS)
        self.assertIn("setInlineStatus(\"Demo paused.\", \"ok\")", JS)

    def test_voice_support_helpers_present(self):
        self.assertIn("function getSpeechRecognitionCtor()", JS)
        self.assertIn('function listenForSpeech(targetInputId, onDone, preferredLang = "auto")', JS)
        self.assertIn("function speakText(text)", JS)


if __name__ == "__main__":
    unittest.main()
