"""Voice assistant preferences, stored in the existing ``settings`` table.

Every key is prefixed ``voice.`` so nothing can collide with the keys the rest of the app already uses.
Values stay strings because that is what the settings table holds; the conversion happens here and nowhere
else, so no caller ever has to remember whether "1" means on or off.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

PREFIX = "voice."

LANGUAGE_ENGLISH = "en"
LANGUAGE_HINDI = "hi"
LANGUAGE_HINGLISH = "hinglish"
LANGUAGES = (LANGUAGE_ENGLISH, LANGUAGE_HINDI, LANGUAGE_HINGLISH)
LANGUAGE_LABELS = {LANGUAGE_ENGLISH: "English", LANGUAGE_HINDI: "Hindi", LANGUAGE_HINGLISH: "Hinglish"}

GENDER_FEMALE = "female"
GENDER_MALE = "male"
GENDERS = (GENDER_FEMALE, GENDER_MALE)

DEFAULTS: dict[str, str] = {
    "enabled": "1",
    "language": LANGUAGE_HINGLISH,
    "gender": GENDER_FEMALE,
    "volume": "80",        # 0-100
    "speed": "100",        # 50-200 percent of normal pace; 100 is the voice's own recording speed
    "pitch": "0",          # semitones, only applied when the engine says it can
    "quiet": "0",          # quiet mode: priority announcements only
    "public": "0",         # public mode: never speak a customer's name
    "engine": "",          # optional override: full path to the speech sidecar
}

_ON = {"1", "true", "yes", "on"}


def _flag(value: str, default: bool) -> bool:
    text = (value or "").strip().lower()
    if text == "":
        return default
    return text in _ON


def _clamped(value: str, low: int, high: int, default: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


@dataclass
class VoiceSettings:
    """A snapshot of the assistant's preferences. Read it, do not mutate the strings at the call sites."""

    enabled: bool = True
    language: str = LANGUAGE_HINGLISH
    gender: str = GENDER_FEMALE
    volume: int = 80
    speed: int = 100
    pitch: int = 0
    quiet: bool = False
    public: bool = False
    engine_path: str = ""
    events: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def load(cls, service) -> "VoiceSettings":
        # service may be None when the data file itself failed to open - the warning still has to be speakable,
        # and at that point there are no stored preferences to read.
        get = service.get_setting if service is not None else (lambda key, default="": default)
        events = {event.name: _flag(get(PREFIX + "event." + event.name, ""), True) for event in _catalogue()}
        language = get(PREFIX + "language", "")
        gender = get(PREFIX + "gender", "")
        return cls(
            enabled=_flag(get(PREFIX + "enabled", ""), True),
            language=language if language in LANGUAGES else DEFAULTS["language"],
            gender=gender if gender in GENDERS else DEFAULTS["gender"],
            volume=_clamped(get(PREFIX + "volume", ""), 0, 100, 80),
            speed=_clamped(get(PREFIX + "speed", ""), 50, 200, 100),
            pitch=_clamped(get(PREFIX + "pitch", ""), -12, 12, 0),
            quiet=_flag(get(PREFIX + "quiet", ""), False),
            public=_flag(get(PREFIX + "public", ""), False),
            engine_path=get(PREFIX + "engine", ""),
            events=events,
        )

    def save(self, service) -> None:
        service.set_setting(PREFIX + "enabled", "1" if self.enabled else "0")
        service.set_setting(PREFIX + "language", self.language)
        service.set_setting(PREFIX + "gender", self.gender)
        service.set_setting(PREFIX + "volume", str(self.volume))
        service.set_setting(PREFIX + "speed", str(self.speed))
        service.set_setting(PREFIX + "pitch", str(self.pitch))
        service.set_setting(PREFIX + "quiet", "1" if self.quiet else "0")
        service.set_setting(PREFIX + "public", "1" if self.public else "0")
        if self.engine_path:
            service.set_setting(PREFIX + "engine", self.engine_path)
        else:
            service.delete_setting(PREFIX + "engine")
        for name, on in self.events.items():
            service.set_setting(PREFIX + "event." + name, "1" if on else "0")

    def resets(self, service) -> "VoiceSettings":
        """Drop every stored preference so the built-in defaults apply again, and return that clean state."""
        for key in _event_keys() | {PREFIX + name for name in DEFAULTS}:
            service.delete_setting(key)
        return type(self)()

    def speaks(self, event_name: str) -> bool:
        """Is this event switched on? Events default to on so a fresh install announces everything."""
        return self.events.get(event_name, True)

    def with_event(self, name: str, on: bool) -> "VoiceSettings":
        events = dict(self.events)
        events[name] = on
        return replace(self, events=events)

    @property
    def length_scale(self) -> float:
        """Piper's speed knob: values above 1 speak slower, so it is the inverse of the user's percentage."""
        return round(100.0 / max(50, min(200, self.speed)), 3)

    @property
    def play_volume(self) -> float:
        return max(0.0, min(1.0, self.volume / 100.0))


def _catalogue():
    """Imported late: the dispatcher imports this module, so a top-level import would be circular."""
    from .voice_event_dispatcher import EVENT_CATALOGUE

    return EVENT_CATALOGUE


def _event_keys() -> set[str]:
    return {PREFIX + "event." + event.name for event in _catalogue()}
