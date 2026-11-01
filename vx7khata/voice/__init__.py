"""Offline voice assistant.

The assistant listens to what the rest of the app does and says it out loud. Nothing here reaches the network:
speech is produced by a bundled, separate-process text-to-speech engine, and every preference lives in the same
SQLite settings table the app already uses.
"""
from __future__ import annotations

from .voice_manager import VoiceManager, announce, current, install
from .voice_settings import VoiceSettings

__all__ = ["VoiceManager", "VoiceSettings", "announce", "current", "install"]
