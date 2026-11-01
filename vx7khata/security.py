"""Optional PIN lock for the application window.

This is an access screen, NOT encryption: the SQLite file itself is not encrypted. It stops a casual person
at an unattended PC from opening the app. The PIN is stored only as a salted PBKDF2-HMAC-SHA256 hash.
A one-time recovery code is created with the PIN so a forgotten PIN does not lock the owner out for good.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import Callable, Optional

from .service import KhataService, ValidationError

PIN_KEY = "pin_hash"
RECOVERY_KEY = "pin_recovery_hash"
ITERATIONS = 200_000
MIN_PIN_LEN = 4
MAX_PIN_LEN = 12
_RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L


def _hash(secret: str, salt: Optional[bytes] = None, iterations: int = ITERATIONS) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def _verify(secret: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _normalise_recovery(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def _check_pin_format(pin: str) -> str:
    pin = str(pin or "")
    if not pin.isdigit() or not MIN_PIN_LEN <= len(pin) <= MAX_PIN_LEN:
        raise ValidationError(f"PIN must be {MIN_PIN_LEN} to {MAX_PIN_LEN} digits")
    return pin


def has_pin(svc: KhataService) -> bool:
    return bool(svc.get_setting(PIN_KEY, ""))


def verify_pin(svc: KhataService, pin: str) -> bool:
    stored = svc.get_setting(PIN_KEY, "")
    return bool(stored) and _verify(str(pin or ""), stored)


def set_pin(svc: KhataService, new_pin: str, current_pin: Optional[str] = None) -> str:
    """Set or change the PIN and return a NEW recovery code (shown to the user once, never stored in clear).

    Changing an existing PIN requires the current PIN.
    """
    new_pin = _check_pin_format(new_pin)
    if has_pin(svc) and not verify_pin(svc, current_pin or ""):
        raise ValidationError("Current PIN is incorrect")
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(12))
    svc.set_setting(PIN_KEY, _hash(new_pin))
    svc.set_setting(RECOVERY_KEY, _hash(raw))
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def _clear_lockout(svc: KhataService) -> None:
    svc.delete_setting(PinGuard.FAILURES_KEY)
    svc.delete_setting(PinGuard.LOCKED_UNTIL_KEY)


def clear_pin(svc: KhataService, current_pin: str) -> None:
    if not verify_pin(svc, current_pin):
        raise ValidationError("PIN is incorrect")
    svc.delete_setting(PIN_KEY)
    svc.delete_setting(RECOVERY_KEY)
    _clear_lockout(svc)


def reset_pin_with_recovery(svc: KhataService, recovery_code: str, new_pin: str) -> str:
    """Set a new PIN using the recovery code. Returns a fresh recovery code (the old one stops working)."""
    new_pin = _check_pin_format(new_pin)
    stored = svc.get_setting(RECOVERY_KEY, "")
    if not stored or not _verify(_normalise_recovery(recovery_code), stored):
        raise ValidationError("Recovery code is incorrect")
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(12))
    svc.set_setting(PIN_KEY, _hash(new_pin))
    svc.set_setting(RECOVERY_KEY, _hash(raw))
    _clear_lockout(svc)
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


class PinGuard:
    """Slows down guessing: 5 wrong tries lock the screen for 30 s, doubling after each further failure.

    The failure count and lock time are saved in the data file, so closing and reopening the app does not
    give a fresh set of attempts.
    """

    FREE_ATTEMPTS = 5
    BASE_LOCK_SECONDS = 30
    FAILURES_KEY = "pin_failures"
    LOCKED_UNTIL_KEY = "pin_locked_until"

    def __init__(self, svc: KhataService, clock: Optional[Callable[[], datetime]] = None):
        self.svc = svc
        self._clock = clock or svc.now
        self.failures = 0
        self.locked_until: Optional[datetime] = None
        self._load()

    def _load(self) -> None:
        try:
            self.failures = max(0, int(self.svc.get_setting(self.FAILURES_KEY, "0") or 0))
        except ValueError:
            self.failures = 0
        raw = self.svc.get_setting(self.LOCKED_UNTIL_KEY, "")
        try:
            self.locked_until = datetime.fromisoformat(raw) if raw else None
            if self.locked_until is not None:
                self.seconds_locked()  # raises TypeError if naive/aware clocks are mixed
        except (ValueError, TypeError):
            self.locked_until = None

    def _save(self) -> None:
        if self.failures:
            self.svc.set_setting(self.FAILURES_KEY, str(self.failures))
        else:
            self.svc.delete_setting(self.FAILURES_KEY)
        if self.locked_until is not None:
            self.svc.set_setting(self.LOCKED_UNTIL_KEY, self.locked_until.isoformat())
        else:
            self.svc.delete_setting(self.LOCKED_UNTIL_KEY)

    def seconds_locked(self) -> int:
        if self.locked_until is None:
            return 0
        remaining = (self.locked_until - self._clock()).total_seconds()
        return max(0, int(remaining + 0.999))

    def attempt(self, pin: str) -> bool:
        """True when the PIN is right. While locked, always False (and the guess is not even checked)."""
        if self.seconds_locked() > 0:
            return False
        if verify_pin(self.svc, pin):
            if self.failures or self.locked_until is not None:
                self.failures = 0
                self.locked_until = None
                self._save()
            return True
        self.failures += 1
        if self.failures >= self.FREE_ATTEMPTS:
            extra = self.failures - self.FREE_ATTEMPTS
            self.locked_until = self._clock() + timedelta(seconds=self.BASE_LOCK_SECONDS * (2 ** extra))
        self._save()
        return False
