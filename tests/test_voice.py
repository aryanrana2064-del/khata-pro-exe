"""Offline voice assistant: phrases, queue, settings, model loader, manager, wiring.

    QT_QPA_PLATFORM=offscreen python -m unittest tests.test_voice -v

Nothing here opens an audio device: the player is replaced with a fake. The real speech engines are only started
by the two bundled-voice tests, which check that the shipped models actually produce a WAV.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAVE_PIPER = importlib.util.find_spec("piper") is not None
HAVE_HINDI = (importlib.util.find_spec("onnxruntime") is not None
              and importlib.util.find_spec("sentencepiece") is not None)

try:
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

from tests.helpers import ServiceTestCase
from vx7khata import voice
from vx7khata.voice import voice_history, voice_model_loader, voice_phrases
from vx7khata.voice import voice_event_dispatcher as dispatcher
from vx7khata.voice.voice_event_dispatcher import BY_NAME, EVENT_CATALOGUE
from vx7khata.voice.voice_queue import VoiceQueue
from vx7khata.voice.voice_settings import LANGUAGES, VoiceSettings


class FakeEngine:
    """Stands in for the Piper sidecar: records what it was asked to say."""

    def __init__(self):
        self.override = ""
        self.last_error = ""
        self.jobs = []          # (text, length_scale)
        self.speakers = []      # speaker id asked for, one per job
        self.answers = []       # results waiting for poll()
        self.cancel_count = 0
        self.closed = False

    def start(self, text, model, length_scale=1.0, volume=1.0, speaker=0):
        token = f"t{len(self.jobs) + 1}"
        self.jobs.append((text, length_scale))
        self.speakers.append(speaker)
        return token

    def deliver(self, ok=True, error=""):
        self.answers.append((f"t{len(self.jobs)}", Path("clip.wav") if ok else None, error))

    def poll(self):
        return self.answers.pop(0) if self.answers else None

    def cancel(self):
        self.cancel_count += 1

    def shutdown(self):
        self.closed = True


class FakePlayer:
    def __init__(self, accept=True):
        self.accept = accept
        self.played = []
        self.playing = False

    def play(self, path, volume):
        if not self.accept:
            return False
        self.played.append((str(path), volume))
        return True

    def is_playing(self):
        return self.playing

    def stop(self):
        self.playing = False


class VoiceTestCase(ServiceTestCase):
    """A service plus a private data folder, so history and models never land in the real APPDATA."""

    def setUp(self):
        super().setUp()
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        env = mock.patch.dict(os.environ, {"VX7_DATA_DIR": self.data.name})
        env.start()
        self.addCleanup(env.stop)
        # The 75 MB voice that ships inside the EXE must not decide what a unit test sees; TestBundledVoice
        # covers the real assets folder on its own.
        assets = mock.patch.object(voice_model_loader, "bundled_dir", lambda: Path(self.data.name) / "bundled")
        assets.start()
        self.addCleanup(assets.stop)
        self.addCleanup(voice.install, None)

    def manager(self, player=None, engine=None):
        manager = voice.VoiceManager(self.svc, engine=engine or FakeEngine(), player=player or FakePlayer())
        voice.install(manager)
        return manager

    @staticmethod
    def ready_library(model=None):
        model = model or voice_model_loader.VoiceModel(
            name="en_US-test", label="Test voice", model=Path("m.onnx"), config=Path("m.onnx.json"),
            language="en")
        return voice_model_loader.VoiceLibrary(models=[model], engine_available=True)


class TestPhrases(unittest.TestCase):
    def test_indian_scales_and_paise(self):
        self.assertEqual(voice_phrases.amount_words(85000, "en"), "eight hundred fifty rupees")
        self.assertEqual(voice_phrases.amount_words(125050, "en"),
                         "one thousand two hundred fifty rupees fifty paise")
        self.assertEqual(voice_phrases.amount_words(12345678, "en"),
                         "one lakh twenty-three thousand four hundred fifty-six rupees seventy-eight paise")
        self.assertEqual(voice_phrases.amount_words(10000000, "en"), "one lakh rupees")

    def test_crore_scale(self):
        self.assertEqual(voice_phrases.amount_words(1000000000, "en"), "one crore rupees")

    def test_sub_rupee_amount_has_no_zero_rupees_prefix(self):
        self.assertEqual(voice_phrases.amount_words(99, "en"), "ninety-nine paise")

    def test_zero_is_speakable(self):
        self.assertEqual(voice_phrases.amount_words(0, "en"), "zero rupees")
        self.assertEqual(voice_phrases.amount_words(0, "hinglish"), "shoonya rupaye")

    def test_negative_amount(self):
        self.assertTrue(voice_phrases.amount_words(-85000, "en").startswith("minus "))

    def test_hindi_uses_devanagari_not_digits(self):
        spoken = voice_phrases.amount_words(85000, "hi")
        self.assertNotIn("8", spoken)
        self.assertNotIn("5", spoken)
        self.assertIn("रुपये", spoken)

    def test_hinglish_is_latin_script(self):
        spoken = voice_phrases.amount_words(85000, "hinglish")
        self.assertTrue(spoken.isascii())
        self.assertTrue(spoken.endswith("rupaye"))


class TestQueue(unittest.TestCase):
    def test_fifo_inside_one_priority(self):
        queue = VoiceQueue()
        for text in ("one", "two", "three"):
            queue.push(text, event="e", priority=1, key=text)
        self.assertEqual([queue.pop().text, queue.pop().text, queue.pop().text], ["one", "two", "three"])

    def test_high_priority_jumps_the_line_but_waits_for_the_current_sentence(self):
        queue = VoiceQueue()
        queue.push("routine", event="a", priority=1, key="1")
        queue.push("warning", event="b", priority=2, key="2")
        queue.push("later", event="c", priority=1, key="3")
        self.assertEqual(queue.pop().text, "warning")
        self.assertEqual(queue.pop().text, "routine")

    def test_duplicate_key_is_dropped(self):
        queue = VoiceQueue()
        self.assertTrue(queue.push("saved", event="entry_added", priority=1, key="entry_added:1"))
        self.assertFalse(queue.push("saved", event="entry_added", priority=1, key="entry_added:1"))

    def test_overflow_drops_the_oldest_routine_line(self):
        queue = VoiceQueue()
        queue.push("warning", event="w", priority=2, key="w")
        for i in range(40):
            queue.push(f"routine-{i}", event="r", priority=0, key=f"r{i}")
        self.assertLessEqual(len(queue), 32)
        texts = [queue.pop().text for _ in range(len(queue))]
        self.assertIn("warning", texts)
        self.assertNotIn("routine-0", texts)

    def test_clear(self):
        queue = VoiceQueue()
        queue.push("a", event="e", priority=1, key="1")
        self.assertEqual(queue.clear(), 1)
        self.assertFalse(queue)


class TestSettings(VoiceTestCase):
    def test_defaults_when_nothing_is_stored(self):
        settings = VoiceSettings.load(self.svc)
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.language, "hinglish")
        self.assertEqual((settings.volume, settings.speed), (80, 100))
        self.assertTrue(settings.speaks("entry_added"))

    def test_round_trip_and_clamping(self):
        VoiceSettings(language="hi", gender="male", volume=55, speed=130, quiet=True, public=True).save(self.svc)
        loaded = VoiceSettings.load(self.svc)
        self.assertEqual((loaded.language, loaded.gender), ("hi", "male"))
        self.assertEqual((loaded.volume, loaded.speed), (55, 130))
        self.assertTrue(loaded.quiet and loaded.public)

        self.svc.set_setting("voice.volume", "500")
        self.svc.set_setting("voice.speed", "1")
        loaded = VoiceSettings.load(self.svc)
        self.assertEqual(loaded.volume, 100)
        self.assertEqual(loaded.speed, 50)

    def test_unknown_language_falls_back(self):
        self.svc.set_setting("voice.language", "klingon")
        self.assertEqual(VoiceSettings.load(self.svc).language, "hinglish")

    def test_speed_percentage_is_inverted_for_the_engine(self):
        self.assertEqual(VoiceSettings(speed=100).length_scale, 1.0)
        self.assertEqual(VoiceSettings(speed=50).length_scale, 2.0)
        self.assertEqual(VoiceSettings(speed=200).length_scale, 0.5)

    def test_per_event_flag(self):
        settings = VoiceSettings().with_event("entry_added", False)
        settings.save(self.svc)
        self.assertFalse(VoiceSettings.load(self.svc).speaks("entry_added"))
        self.assertTrue(VoiceSettings.load(self.svc).speaks("entry_deleted"))

    def test_restore_defaults_removes_every_voice_row(self):
        VoiceSettings(language="en", volume=10, quiet=True).with_event("entry_added", False).save(self.svc)
        clean = VoiceSettings().resets(self.svc)
        self.assertEqual(clean.language, "hinglish")
        self.assertFalse(any(key.startswith("voice.") for key in self._voice_keys()))

    def _voice_keys(self):
        rows = self.svc.conn.execute("SELECT key FROM settings WHERE key LIKE 'voice.%'").fetchall()
        return [row["key"] for row in rows]


class TestDispatcher(unittest.TestCase):
    def values(self):
        return {"amount": 85000, "balance": 3509600, "customer": "Ramesh", "type": "UDHAAR", "count": 3}

    def test_every_event_speaks_in_every_language(self):
        for event in EVENT_CATALOGUE:
            for language in ("en", "hi", "hinglish"):
                spoken = dispatcher.render(event, self.values(), language)
                self.assertTrue(spoken, f"{event.name} has no {language} sentence")
                self.assertNotIn("{", spoken, f"{event.name} left a placeholder unfilled")

    def test_names_are_unique(self):
        names = [event.name for event in EVENT_CATALOGUE]
        self.assertEqual(len(names), len(set(names)))

    def test_public_mode_hides_the_customer_name(self):
        event = BY_NAME["balance_updated"]
        normal = dispatcher.render(event, {"customer": "Ramesh", "balance": 500000}, "en")
        public = dispatcher.render(event, {"customer": "Ramesh", "balance": 500000}, "en", public=True)
        self.assertIn("Ramesh", normal)
        self.assertNotIn("Ramesh", public)
        self.assertIn("the customer", public)

    def test_amounts_are_words(self):
        spoken = dispatcher.render(BY_NAME["payment_received"], {"amount": 85000, "balance": 3509600}, "en")
        self.assertIn("eight hundred fifty rupees", spoken)
        self.assertIn("thirty-five thousand ninety-six rupees", spoken)

    def test_type_words_follow_the_language(self):
        self.assertIn("udhaar", dispatcher.render(BY_NAME["entry_added"], {"type": "UDHAAR", "amount": 100},
                                                  "hinglish"))
        self.assertIn("debit", dispatcher.render(BY_NAME["entry_added"], {"type": "UDHAAR", "amount": 100}, "en"))

    def test_count_gets_its_noun(self):
        self.assertIn("1 entry", dispatcher.render(BY_NAME["daily_posted"], {"count": 1}, "en"))
        self.assertIn("4 entries", dispatcher.render(BY_NAME["daily_posted"], {"count": 4}, "en"))

    def test_missing_value_stays_silent_instead_of_crashing(self):
        self.assertEqual(dispatcher.render(BY_NAME["payment_received"], {}, "en"), "")


class TestModelLoader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"VX7_DATA_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.folder = voice_model_loader.user_dir()
        self.folder.mkdir(parents=True, exist_ok=True)
        # Scan an empty "installed with the EXE" folder as well, so these tests describe the loader and not
        # whatever voice happens to be bundled in the repo.
        assets = mock.patch.object(voice_model_loader, "bundled_dir", lambda: Path(self.tmp.name) / "bundled")
        assets.start()
        self.addCleanup(assets.stop)

    def write(self, name, config=True, size=voice_model_loader.MIN_MODEL_BYTES + 1, manifest=None):
        (self.folder / f"{name}.onnx").write_bytes(b"\0" * size)
        if config:
            (self.folder / f"{name}.onnx.json").write_text(json.dumps({"audio": {}}), encoding="utf-8")
        if manifest is not None:
            (self.folder / "voices.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_a_complete_voice_is_usable(self):
        self.write("en_US-good")
        library = voice_model_loader.discover(engine_available=True)
        self.assertEqual(library.status, "")
        self.assertTrue(library.pick("en").usable)

    def test_truncated_download_is_marked_unusable_with_a_reason(self):
        self.write("en_US-partial", size=1000)
        library = voice_model_loader.discover(engine_available=True)
        model = library.models[0]
        self.assertFalse(model.usable)
        self.assertIn("incomplete", model.reason)

    def test_missing_config(self):
        self.write("en_US-noconfig", config=False)
        self.assertIn("configuration file is missing", voice_model_loader.discover(True).models[0].reason)

    def test_corrupt_config(self):
        self.write("en_US-badconfig")
        (self.folder / "en_US-badconfig.onnx.json").write_text("{not json", encoding="utf-8")
        self.assertIn("damaged", voice_model_loader.discover(True).models[0].reason)

    def test_manifest_sets_language_and_gender(self):
        self.write("weird-name", manifest={"weird-name": {"language": "hi", "gender": "male", "label": "Hindi"}})
        model = voice_model_loader.discover(True).models[0]
        self.assertEqual((model.language, model.gender, model.label), ("hi", "male", "Hindi"))

    def test_file_name_decides_the_language_without_a_manifest(self):
        self.write("hi_IN-rohan")
        self.assertEqual(voice_model_loader.discover(True).models[0].language, "hi")

    def test_hinglish_prefers_hindi_then_english(self):
        self.write("hi_IN-x")
        self.write("en_US-y")
        library = voice_model_loader.discover(True)
        self.assertEqual(library.pick("hinglish").language, "hi")
        self.assertEqual(library.pick("en").language, "en")

    def test_no_model_says_so(self):
        self.assertEqual(voice_model_loader.discover(True).status, "No voice model is installed.")

    def test_no_engine_says_so_first(self):
        self.write("en_US-good")
        self.assertEqual(voice_model_loader.discover(False, "Engine missing.").status, "Engine missing.")

    def test_gender_picks_a_speaker_inside_a_many_voiced_model(self):
        self.write("en_US-many", manifest={"en_US-many": {
            "language": "en", "gender": "neutral", "speakers": 904,
            "gender_speakers": {"female": 307, "male": 805}}})
        library = voice_model_loader.discover(True)
        self.assertEqual(library.pick("en", "male").speaker_for("male"), 805)
        self.assertEqual(library.pick("en", "female").speaker_for("female"), 307)

    def test_a_voice_that_is_only_one_gender_wins_over_a_speaker_inside_another(self):
        many = {"en_US-many": {"language": "en", "gender": "neutral", "gender_speakers": {"male": 805}}}
        many["en_US-bloke"] = {"language": "en", "gender": "male", "label": "Bloke"}
        self.write("en_US-many", manifest=many)
        self.write("en_US-bloke")
        self.assertEqual(voice_model_loader.discover(True).pick("en", "male").name, "en_US-bloke")

    def test_a_gender_the_model_lacks_falls_back_to_speaker_zero(self):
        self.write("en_US-solo", manifest={"en_US-solo": {"language": "en", "gender": "female"}})
        model = voice_model_loader.discover(True).pick("en", "male")
        self.assertEqual(model.speaker_for("male"), 0)

    def test_a_speaker_id_that_cannot_be_counted_is_dropped(self):
        self.write("en_US-odd", manifest={"en_US-odd": {"language": "en", "gender_speakers": {"male": "eight oh five"}}})
        self.assertEqual(voice_model_loader.discover(True).models[0].speakers_by_gender, {})

    def test_the_speaker_count_comes_from_the_model_configuration(self):
        (self.folder / "en_US-many.onnx").write_bytes(b"\0" * (voice_model_loader.MIN_MODEL_BYTES + 1))
        (self.folder / "en_US-many.onnx.json").write_text(json.dumps({"num_speakers": 904}), encoding="utf-8")
        self.assertEqual(voice_model_loader.discover(True).models[0].speakers, 904)

    def bundle(self, name="hi_IN-pocket", graphs=voice_model_loader.BUNDLE_GRAPHS):
        folder = self.folder / name
        folder.mkdir()
        for graph in graphs:
            (folder / graph).write_bytes(b"\0" * (voice_model_loader.MIN_MODEL_BYTES + 1))
        (folder / "tokenizer.model").write_bytes(b"\0" * 16)
        (folder / "bundle.json").write_text(json.dumps({"mimi_state": []}), encoding="utf-8")
        return folder

    def test_a_voice_folder_is_discovered_as_a_voice(self):
        self.bundle()
        model = voice_model_loader.discover(True).models[0]
        self.assertTrue(model.usable)
        self.assertEqual((model.language, model.model.is_dir()), ("hi", True))

    def test_hinglish_prefers_the_hindi_folder_over_english(self):
        self.bundle()
        self.write("en_US-y")
        self.assertEqual(voice_model_loader.discover(True).pick("hinglish").name, "hi_IN-pocket")

    def test_a_folder_missing_one_graph_is_not_usable(self):
        self.bundle(graphs=voice_model_loader.BUNDLE_GRAPHS[1:])
        model = voice_model_loader.discover(True).models[0]
        self.assertFalse(model.usable)
        self.assertIn("text_conditioner.onnx", model.reason)

    def test_a_voice_folder_and_a_voice_file_use_different_sidecars(self):
        from vx7khata.voice.offline_tts_engine import sidecar_command

        folder = sidecar_command(self.bundle())
        self.assertIn("hindi_sidecar", " ".join(folder))
        self.assertIn("--bundle", folder)
        single = sidecar_command(Path("m.onnx"))
        self.assertIn("piper_sidecar", " ".join(single))
        self.assertIn("--model", single)


class TestBundledVoice(unittest.TestCase):
    """What the installer actually ships: a voice has to be complete, distributable in a paid app, and audible.

    Only the bundled folder is read here - a voice the customer dropped into %APPDATA% must not hide a broken
    bundle - so the data folder is pointed at an empty temporary directory.
    """

    def setUp(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        env = mock.patch.dict(os.environ, {"VX7_DATA_DIR": empty.name})
        env.start()
        self.addCleanup(env.stop)

    def manifest(self):
        folder = voice_model_loader.bundled_dir()
        return folder, json.loads((folder / "voices.json").read_text(encoding="utf-8"))

    def test_every_bundled_voice_is_complete_and_attributed(self):
        folder, manifest = self.manifest()
        models = sorted(folder.glob("*.onnx"))
        self.assertTrue(models, "the EXE would ship with no voice model at all")
        for model in models:
            name = model.name[: -len(".onnx")]
            self.assertGreaterEqual(model.stat().st_size, voice_model_loader.MIN_MODEL_BYTES, f"{name}: truncated")
            self.assertTrue(model.with_suffix(".onnx.json").is_file(), f"{name}: no configuration")
            entry = manifest.get(name)
            self.assertIsNotNone(entry, f"{name} is bundled without any attribution")
            self.assertTrue(str(entry.get("label", "")).strip(), name)
            self.assertIn(str(entry.get("language")), LANGUAGES, name)
            self.assertTrue(str(entry.get("license", "")).strip(), f"{name}: no licence recorded")

    def test_no_restricted_licence_is_bundled(self):
        # The three Piper Hindi voices are CC BY-NC-SA and cannot go into a paid product; if one ever lands in
        # the assets folder this test is what stops the installer from shipping it.
        _folder, manifest = self.manifest()
        forbidden = ("by-nc", "nc-sa", "noncommercial", "non-commercial", "research", "internal")
        for name, entry in manifest.items():
            licence = str(entry.get("license", "")).lower().replace(" ", "")
            self.assertFalse(any(word in licence for word in forbidden), f"{name}: {licence} cannot ship in a paid app")

    def test_the_bundled_library_is_ready_to_speak_english(self):
        library = voice_model_loader.discover(engine_available=True)
        self.assertEqual(library.status, "")
        self.assertIsNotNone(library.pick("en"))

    def test_both_genders_are_installed_voices_not_promises(self):
        # The Voice page offers Female and Male, so the shipped manifest has to point both at a speaker that
        # really exists inside the model, and say where the gender of that speaker was read from.
        folder, manifest = self.manifest()
        library = voice_model_loader.discover(engine_available=True)
        for gender in ("female", "male"):
            model = library.pick("en", gender)
            self.assertIsNotNone(model, f"no bundled voice answers {gender}")
            self.assertIn(gender, model.speakers_by_gender, f"{model.name} cannot speak as {gender}")
        for name, entry in manifest.items():
            picks = entry.get("gender_speakers") or {}
            if not picks:
                continue
            config = json.loads((folder / f"{name}.onnx.json").read_text(encoding="utf-8"))
            ids = set((config.get("speaker_id_map") or {}).values())
            self.assertTrue(str(entry.get("gender_attribution", "")).strip(), f"{name}: gender claimed with no source")
            for gender, speaker in picks.items():
                self.assertIn(gender, ("female", "male"), name)
                self.assertIn(int(speaker), ids, f"{name}: no speaker {speaker} inside the model")

    def test_the_hindi_voice_ships_with_its_licence_and_papers(self):
        # Every Piper Hindi voice is CC BY-NC-SA. This bundle is the CC BY 4.0 alternative, so the licence and the
        # exporter's attribution must travel with the weights.
        folder = voice_model_loader.bundled_dir() / "hi_IN-pocket"
        self.assertTrue(folder.is_dir(), "the installer would ship no Hindi voice")
        for graph in voice_model_loader.BUNDLE_GRAPHS:
            self.assertGreater((folder / graph).stat().st_size, voice_model_loader.MIN_MODEL_BYTES, graph)
        for name in ("LICENSE.md", "ATTRIBUTION.md", "NOTICE.txt", "tokenizer.model", "inaya.safetensors",
                     "bundle.json"):
            self.assertTrue((folder / name).is_file(), f"{name} belongs beside the Hindi voice")
        entry = self.manifest()[1].get("hi_IN-pocket", {})
        self.assertEqual(entry.get("language"), "hi")
        self.assertIn("CC BY 4.0", str(entry.get("license")))
        library = voice_model_loader.discover(engine_available=True)
        self.assertEqual(library.pick("hi").name, "hi_IN-pocket")
        self.assertEqual(library.pick("hinglish").language, "hi")
        self.assertEqual(library.pick("en").language, "en")

    @unittest.skipUnless(HAVE_HINDI, "the Hindi engine is not installed")
    def test_the_hindi_voice_produces_audio_without_the_internet(self):
        from vx7khata.voice.offline_tts_engine import SidecarEngine

        model = voice_model_loader.discover(engine_available=True).pick("hi")
        self.assertIsNotNone(model)
        with tempfile.TemporaryDirectory() as tmp:
            engine = SidecarEngine(temp_dir=Path(tmp))
            try:
                engine.start("आपके खाते में आठ सौ पचास रुपये जमा हुए हैं।", model.model)
                deadline = time.time() + 180
                answer = None
                while time.time() < deadline and answer is None:
                    answer = engine.poll()
                    if answer is None:
                        time.sleep(0.05)
                self.assertIsNotNone(answer, "the Hindi sidecar never answered")
                token, path, error = answer
                self.assertFalse(error, "the Hindi sidecar reported an error")
                self.assertIsNotNone(path)
                with wave.open(str(path)) as handle:
                    seconds = handle.getnframes() / handle.getframerate()
                self.assertGreater(seconds, 0.8, "the Hindi voice produced almost no audio")
                self.assertLess(seconds, 12.0)
            finally:
                engine.shutdown()

    @unittest.skipUnless(HAVE_PIPER, "the speech engine is not installed")
    def test_the_sidecar_produces_audio_from_local_files_only(self):
        from vx7khata.voice.offline_tts_engine import SidecarEngine

        model = voice_model_loader.discover(engine_available=True).pick("en")
        self.assertIsNotNone(model)
        with tempfile.TemporaryDirectory() as tmp:
            engine = SidecarEngine(temp_dir=Path(tmp))
            try:
                engine.start("New entry saved. Eight hundred fifty rupees.", model.model)
                deadline = time.time() + 90
                answer = None
                while time.time() < deadline and answer is None:
                    answer = engine.poll()
                    if answer is None:
                        time.sleep(0.05)
            finally:
                engine.shutdown()
            self.assertIsNotNone(answer, "the sidecar never answered")
            _, path, error = answer
            self.assertIsNotNone(path, f"no audio: {error}")
            self.assertGreater(path.stat().st_size, 4096, "the clip is silence, not speech")


class TestManager(VoiceTestCase):
    def ready(self, **kw):
        manager = self.manager(**kw)
        manager.library = self.ready_library()
        return manager

    def test_master_switch_off_is_silent(self):
        manager = self.ready()
        manager.settings.enabled = False
        self.assertFalse(manager.announce("entry_added", amount=100))
        self.assertFalse(manager.queue)

    def test_per_event_switch_off_is_silent(self):
        manager = self.ready()
        manager.settings = manager.settings.with_event("entry_added", False)
        self.assertFalse(manager.announce("entry_added", amount=100))
        self.assertTrue(manager.announce("entry_deleted"))

    def test_quiet_mode_keeps_warnings_only(self):
        manager = self.ready()
        manager.settings.quiet = True
        self.assertFalse(manager.announce("entry_added", amount=100))
        self.assertTrue(manager.announce("backup_failed"))

    def test_high_priority_is_spoken_first(self):
        manager = self.ready()
        manager.settings.language = "en"
        manager.announce("entry_added", amount=100)
        manager.announce("pin_wrong")
        self.assertTrue(manager.queue.pop().text.startswith("Wrong PIN"))

    def test_same_event_is_not_said_twice(self):
        manager = self.ready()
        self.assertTrue(manager.announce("entry_deleted"))
        self.assertFalse(manager.announce("entry_deleted"))

    def test_playback_is_recorded_in_history(self):
        manager = self.ready()
        manager.announce("balance_updated", customer="Ramesh", balance=500000)
        manager._pump()
        manager.engine.deliver()
        manager._pump()
        self.assertEqual(len(manager.player.played), 1)
        record = voice_history.recent(1)[0]
        self.assertEqual(record["event"], "balance_updated")
        self.assertTrue(record["spoken"])

    def test_speed_setting_reaches_the_engine(self):
        manager = self.ready()
        manager.settings.speed = 50
        manager.announce("entry_deleted")
        manager._pump()
        self.assertAlmostEqual(manager.engine.jobs[0][1], 2.0)

    def test_the_gender_choice_reaches_the_engine_as_a_speaker(self):
        manager = self.ready()
        manager.library = voice_model_loader.VoiceLibrary(models=[voice_model_loader.VoiceModel(
            name="en_US-many", label="Many voices", model=Path("m.onnx"), config=Path("m.onnx.json"),
            language="en", gender="neutral", speakers=904,
            speakers_by_gender={"female": 307, "male": 805})], engine_available=True)

        def say(event, **values):
            manager.announce(event, **values)
            manager._pump()
            manager.engine.deliver()
            manager._pump()

        manager.settings.gender = "male"
        say("entry_deleted")
        self.assertEqual(manager.engine.speakers, [805])
        manager.settings.gender = "female"
        say("balance_updated", customer="Ramesh", balance=10000)
        self.assertEqual(manager.engine.speakers, [805, 307])

    def test_the_assistant_names_the_voice_it_is_really_using(self):
        manager = self.ready()
        manager.settings.language = "en"
        detail = manager.information()
        self.assertIn("Test voice", detail)
        self.assertIn("Audio is made on this PC", detail)

    def test_missing_audio_device_is_recorded_not_raised(self):
        manager = self.ready(player=FakePlayer(accept=False))
        manager.announce("entry_deleted")
        manager._pump()
        manager.engine.deliver()
        manager._pump()
        self.assertFalse(voice_history.recent(1)[0]["spoken"])

    def test_repeated_failures_stop_the_loop(self):
        manager = self.ready()
        for _ in range(3):
            manager.announce("entry_deleted")
            manager._pump()
            manager.engine.deliver(ok=False, error="the model file is incomplete")
            manager._pump()
            manager.queue.clear()
            manager._in_flight = None
        self.assertTrue(manager.engine_down)
        self.assertIn("incomplete", manager.status())
        self.assertFalse(manager.announce("entry_added", amount=1))

    def test_reload_clears_a_previous_failure(self):
        manager = self.ready()
        manager.engine_down = True
        manager._down_reason = "broken"
        manager.reload()
        manager.library = self.ready_library()
        self.assertFalse(manager.engine_down)
        self.assertTrue(manager.available)

    def test_mute_stops_speech_and_queues(self):
        manager = self.ready()
        manager.announce("entry_deleted")
        manager.set_muted(True)
        self.assertFalse(manager.queue)
        self.assertEqual(manager.status(), "Muted.")

    def test_no_model_means_no_speech(self):
        manager = self.manager()
        manager.library = voice_model_loader.VoiceLibrary(models=[], engine_available=True)
        self.assertFalse(manager.available)
        self.assertIn("No voice model", manager.status())
        self.assertFalse(manager.announce("entry_added", amount=100))

    def test_test_sentence_is_spoken_even_when_switched_off(self):
        manager = self.ready(player=FakePlayer())
        manager.settings.enabled = False
        self.assertTrue(manager.test())
        manager._pump()
        self.assertTrue(manager.engine.jobs)

    def test_public_mode_hides_the_name_in_the_queue(self):
        manager = self.ready()
        manager.settings.public = True
        manager.announce("customer_added", customer="Ramesh Kumar")
        self.assertNotIn("Ramesh", manager.queue.pop().text)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TestQtPlayer(unittest.TestCase):
    """The one class here that is not a fake.

    Qt renamed QSoundEffect's status call in this very PySide6 line, so the player is checked against the real
    widget: it must accept a valid clip and settle back to idle without raising anything at the timer.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def clip(self) -> Path:
        import math
        import struct
        import wave

        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        path = Path(folder) / "beep.wav"
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(i / 9))) for i in range(1600)))
        return path

    def test_a_real_clip_is_accepted_and_settles(self):
        from vx7khata.voice.offline_tts_engine import QtPlayer

        player = QtPlayer()
        self.assertTrue(player.play(self.clip(), 0.8), "the player refused a valid wav")
        for _ in range(150):
            self.app.processEvents()
            if not player.is_playing():
                break
            time.sleep(0.02)
        player.stop()
        self.assertFalse(player.is_playing())


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TestSettingsPanel(VoiceTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        from vx7khata.ui.main_window import MainWindow
        window = MainWindow(self.svc)
        self.addCleanup(window.close)
        window.warn = lambda title, text: None
        window.info = lambda title, text: None
        window.confirm = lambda *args, **kwargs: True
        return window

    @staticmethod
    def speak(manager):
        """Work the queue the way the QTimer would, and return what the engine was asked to say.

        A UI action only queues a sentence; the pump runs on its own timer and offscreen tests never see that
        tick, so each call below stands in for one: hand the sentence over, get the clip back, play it.
        """
        for _ in range(max(1, len(manager.queue))):
            manager._pump()
            manager.engine.deliver()
            manager._pump()
        return [text for text, _ in manager.engine.jobs]

    def test_card_has_every_control(self):
        window = self.window()
        self.assertEqual(len(window.voice_event_boxes), len(EVENT_CATALOGUE))
        self.assertFalse(window.voice_pitch.isEnabled())          # the engine has no pitch, so it is not faked
        self.assertEqual(window.voice_language.count(), 3)
        self.assertEqual(window.voice_gender.count(), 2)
        self.assertEqual((window.voice_volume.minimum(), window.voice_volume.maximum()), (0, 100))

    def test_changes_are_saved_and_reloaded(self):
        window = self.window()
        manager = self.manager()
        window._refresh_voice_settings()
        window.voice_language.setCurrentIndex(window.voice_language.findData("en"))
        window.voice_volume.setValue(55)
        window.voice_quiet.setChecked(True)
        window.voice_event_boxes["app_ready"].setChecked(False)
        self.assertEqual(self.svc.get_setting("voice.language"), "en")
        self.assertEqual(self.svc.get_setting("voice.volume"), "55")
        self.assertEqual(self.svc.get_setting("voice.quiet"), "1")
        self.assertEqual(self.svc.get_setting("voice.event.app_ready"), "0")
        self.assertEqual(manager.settings.language, "en")
        self.assertFalse(manager.settings.speaks("app_ready"))

    def test_panel_shows_why_it_is_silent(self):
        window = self.window()
        window._refresh_voice_settings()
        self.assertIn("not running", window.voice_status.text())
        manager = self.manager()
        manager.library = self.ready_library()
        window._refresh_voice_settings()
        self.assertIn("Ready", window.voice_status.text())

    def test_saving_an_entry_announces_it(self):
        from PySide6.QtCore import QDate
        window = self.window()
        refusals = []
        window.warn = lambda title, text: refusals.append(text)
        manager = self.manager()
        manager.library = self.ready_library()
        customer = self.svc.add_customer("Ramesh")
        window.refresh_all()      # the entry form's customer list only holds customers that already exist
        window.entry_form.select_customer(customer.id)
        window.entry_form.date.setDate(QDate(2026, 10, 3))
        window.entry_form.item.setText("Atta")
        window.entry_form.amount.setText("850")
        window._save_entry()
        self.assertEqual(refusals, [])
        texts = self.speak(manager)
        self.assertTrue(texts, "nothing was spoken after saving an entry")
        self.assertIn("aath sau pachaas rupaye", texts[0])

    def test_backup_announces_success(self):
        window = self.window()
        manager = self.manager()
        manager.library = self.ready_library()
        window._backup_now()
        self.assertTrue(any("Backup" in text for text in self.speak(manager)))

    def test_failed_export_is_not_announced_as_a_success(self):
        window = self.window()
        manager = self.manager()
        manager.library = self.ready_library()
        manager.settings.language = "en"
        window._export_path = lambda *args: os.path.join(self.tmp.name, "out.pdf")

        def boom(path):
            raise OSError("disk full")

        window._offer_open = lambda path: None
        window._run_export("Statement", "x.pdf", "pdf", boom)
        texts = self.speak(manager)
        self.assertTrue(any("could not be saved" in text for text in texts))
        self.assertFalse(any(text.startswith("Report saved") for text in texts))


if __name__ == "__main__":
    unittest.main()
