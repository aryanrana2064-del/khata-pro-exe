"""The one object the rest of the app talks to.

Call sites stay a single line - ``voice.announce("entry_added", amount=txn.amount_paise, ...)`` - and this class
decides whether that is worth saying, when to say it, and in which words. Nothing here raises into a caller: a
broken microphone or a missing model must never stop a shopkeeper from saving an entry.
"""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QCoreApplication, QObject, QTimer

from . import voice_history
from .offline_tts_engine import SidecarEngine, make_player, piper_installed
from .voice_event_dispatcher import BY_NAME, PRIORITY_HIGH, render
from .voice_model_loader import VoiceModel, discover
from .voice_queue import VoiceQueue
from .voice_settings import LANGUAGE_LABELS, VoiceSettings

PUMP_INTERVAL_MS = 120
MAX_ENGINE_FAILURES = 3      # consecutive syntheses that come back with no audio before we stop trying

_MANAGER: Optional["VoiceManager"] = None


def install(manager: "VoiceManager | None") -> None:
    """Make this the assistant the whole app announces through (or remove it again)."""
    global _MANAGER
    _MANAGER = manager


def current() -> "VoiceManager | None":
    return _MANAGER


def announce(event: str, **values) -> bool:
    """Say one thing out loud. Safe to call before any window exists - it just does nothing then."""
    manager = _MANAGER
    return manager.announce(event, **values) if manager else False


class VoiceManager(QObject):
    """Owns the queue, the engine and the speaker. Driven by a QTimer, like the rest of this app's background work."""

    def __init__(self, service, engine=None, player=None, parent: QObject | None = None):
        super().__init__(parent)
        self.service = service
        self.settings = VoiceSettings.load(service)
        self.queue = VoiceQueue()
        self.engine = engine if engine is not None else SidecarEngine(override=self.settings.engine_path)
        self.player = player if player is not None else make_player()
        self.library = discover(engine_available=piper_installed(),
                               engine_error="The speech engine is not installed.")
        self.muted = False
        self._in_flight: Optional[str] = None
        self._current_event = ""
        self._current_text = ""
        self._failures = 0
        self.engine_down = False
        self._down_reason = ""
        self._unavailable_logged = False
        self._timer = QTimer(self)
        self._timer.setInterval(PUMP_INTERVAL_MS)
        self._timer.timeout.connect(self._pump)

    # ---- configuration ----------------------------------------------------
    def reload(self) -> None:
        """Pick up settings the user just changed, without restarting the app."""
        self.settings = VoiceSettings.load(self.service)
        self.engine.override = self.settings.engine_path
        self.library = discover(engine_available=piper_installed(),
                                engine_error="The speech engine is not installed.")
        self.engine_down = False
        self._failures = 0
        self._down_reason = ""
        self._unavailable_logged = False

    def voice_for(self) -> VoiceModel | None:
        return self.library.pick(self.settings.language, self.settings.gender)

    @property
    def available(self) -> bool:
        return (not self.engine_down and self.library.status == "" and self.voice_for() is not None
                and self.player is not None)

    def status(self) -> str:
        """Why the assistant is silent, or "" when it should be talking."""
        if not self.settings.enabled:
            return "Voice assistant is switched off."
        if self.muted:
            return "Muted."
        if self.engine_down:
            return self._down_reason or self.engine.last_error or "The speech engine stopped working."
        if self.library.status:
            return self.library.status
        if not self.voice_for():
            return f"No {self.settings.language} voice model is installed."
        if self.player is None:
            return "No audio device is available."
        return ""

    def information(self) -> str:
        """What the assistant would speak, with which voice, and where the audio is made."""
        model = self.voice_for()
        installed = ", ".join(sorted(m.label for m in self.library.models if m.usable)) or "none"
        parts = [f"Speaks {LANGUAGE_LABELS.get(self.settings.language, self.settings.language)}",
                 f"Installed voices: {installed}", f"Voice: {self._gender_line(model)}"]
        if model is not None and model.license:
            parts.append(f"Licence: {model.license}")
        parts.append("Audio is made on this PC" if self.available else "Not speaking right now")
        return "   ·   ".join(parts)

    def _gender_line(self, model: VoiceModel | None) -> str:
        """Say which voice the gender choice actually produces instead of pretending every voice has both."""
        if model is None:
            return "none installed"
        wanted = self.settings.gender
        if wanted in model.speakers_by_gender:
            return f"{wanted} speaker in {model.label}"
        if model.gender == wanted:
            return f"{wanted} - {model.label}"
        return f"{model.label} (no {wanted} voice installed, so this one is used)"

    # ---- the only call sites the rest of the app needs --------------------
    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def announce(self, event: str, **values) -> bool:
        """Queue an announcement. Returns True when the sentence was accepted, False when deliberately dropped."""
        definition = BY_NAME.get(event)
        if definition is None:
            return False
        if not self.settings.enabled or self.muted or not self.settings.speaks(definition.name):
            return False
        if self.settings.quiet and definition.priority < PRIORITY_HIGH:
            return False
        if not self.available:
            self._note_unavailable()
            return False
        text = render(definition, values, self.settings.language, public=self.settings.public)
        if not text:
            return False
        key = f"{event}:" + ",".join(f"{k}={values[k]}" for k in sorted(values))
        queued = self.queue.push(text, event=definition.name, priority=definition.priority, key=key)
        if not queued:
            voice_history.record(event, text, False, "duplicate or queue full")
        return queued

    def _note_unavailable(self) -> None:
        """One history line per outage, not one per event."""
        if self._unavailable_logged:
            return
        self._unavailable_logged = True
        voice_history.record("unavailable", "", False, self.status())

    def test(self) -> bool:
        """Say something the user can hear, so they can set the volume before they trust it."""
        sample = {"hi": "यह आवाज़ की परीक्षा है।", "hinglish": "Yeh voice test hai.", "en": "This is a voice test."}
        text = sample.get(self.settings.language, sample["hinglish"])
        return self.queue.push(text, event="test", priority=PRIORITY_HIGH + 1, key="voice-test")

    def clear(self) -> int:
        return self.queue.clear()

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        if muted:
            self.queue.clear()
            if self.player is not None:
                self.player.stop()

    def shutdown(self, grace_ms: int = 1500) -> None:
        """Stop taking work, then give what is already waiting a short moment to be said - closing the app should
        not cut the last sentence off. After the grace period nothing else is waited for."""
        self._timer.stop()
        deadline = time.monotonic() + grace_ms / 1000.0
        while time.monotonic() < deadline:
            if not (self.queue or self._in_flight is not None or self._busy()):
                break
            self._pump()
            QCoreApplication.processEvents()
            time.sleep(0.02)
        self.queue.clear()
        self.engine.cancel()
        self.engine.shutdown()

    # ---- the pump ---------------------------------------------------------
    def _pump(self) -> None:
        result = self.engine.poll()
        if result is not None:
            token, path, error = result
            if token == self._in_flight:
                self._in_flight = None
            if path is not None and self.player is not None:
                try:
                    played = self.player.play(path, self.settings.play_volume)
                except Exception as exc:  # noqa: BLE001 - this runs on a timer; a Qt surprise must not escape
                    played, error = False, f"the audio player failed: {exc}"
                if played:
                    self._failures = 0
                    self.engine_down = False
                    self._down_reason = ""
                    self._unavailable_logged = False
                    voice_history.record(self._current_event, self._current_text, True)
                else:
                    # No sound card, or a device unplugged mid-session: counting it alongside engine faults
                    # stops the assistant re-synthesising every entry against a speaker that is not there.
                    self._count_failure("the audio device refused the clip")
            elif path is None:
                self._count_failure(error or "no audio produced")
        if self._busy():
            return
        if self._in_flight is not None:
            return
        if not self.queue:
            return
        self._start_next()

    def _busy(self) -> bool:
        """Is the speaker still holding the queue? A player that throws counts as free rather than busy."""
        if self.player is None:
            return False
        try:
            return self.player.is_playing()
        except Exception:  # noqa: BLE001 - this runs on a timer too
            return False

    def _count_failure(self, reason: str) -> None:
        """Repeated failures are not retried event by event - the loop is switched off until the settings panel
        reloads, or until a manual Test Voice works again."""
        self._failures += 1
        self._down_reason = reason
        voice_history.record(self._current_event, self._current_text, False, reason)
        if self._failures < MAX_ENGINE_FAILURES:
            return
        self.engine_down = True
        self.queue.clear()
        self.engine.cancel()

    def _start_next(self) -> None:
        entry = self.queue.pop()
        if entry is None:
            return
        model = self.voice_for()
        if model is None:
            voice_history.record(entry.event, entry.text, False, "no voice model installed")
            return
        self._current_event = entry.event
        self._current_text = entry.text
        self._in_flight = self.engine.start(entry.text, model.model, length_scale=self.settings.length_scale,
                                            volume=1.0, speaker=model.speaker_for(self.settings.gender))
