"""Tests for voicelib discovery. Run: python -m unittest test_voicelib"""

import os
import tempfile
import unittest

import voicelib


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _touch(self, name):
        open(os.path.join(self.dir, name), "w").close()

    def test_missing_folder_returns_empty(self):
        self.assertEqual(voicelib._discover(os.path.join(self.dir, "nope")), {})

    def test_empty_folder_returns_empty(self):
        self.assertEqual(voicelib._discover(self.dir), {})

    def test_discovers_by_stem(self):
        self._touch("steve.wav")
        found = voicelib._discover(self.dir)
        self.assertEqual(set(found), {"steve"})
        self.assertTrue(found["steve"].endswith("steve.wav"))

    def test_skips_non_audio_and_txt_sidecars(self):
        self._touch("steve.wav")
        self._touch("steve.txt")
        self._touch("notes.md")
        self.assertEqual(set(voicelib._discover(self.dir)), {"steve"})

    def test_extension_preference_wav_beats_mp3(self):
        # Same stem in two formats -> the earlier-ranked (.wav) wins.
        self._touch("jarvis.mp3")
        self._touch("jarvis.wav")
        found = voicelib._discover(self.dir)
        self.assertTrue(found["jarvis"].endswith("jarvis.wav"))


class RegistryTests(unittest.TestCase):
    """voices()/chimes() are thin wrappers that discover from the configured
    dirs; point those dirs at a temp folder and confirm they find dropped files."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self._orig = (voicelib.VOICES_DIR, voicelib.CHIMES_DIR)

    def tearDown(self):
        voicelib.VOICES_DIR, voicelib.CHIMES_DIR = self._orig
        self._tmp.cleanup()

    def test_voices_reads_voices_dir(self):
        voicelib.VOICES_DIR = self.dir
        open(os.path.join(self.dir, "steve.wav"), "w").close()
        self.assertEqual(set(voicelib.voices()), {"steve"})

    def test_chimes_reads_chimes_dir(self):
        voicelib.CHIMES_DIR = self.dir
        open(os.path.join(self.dir, "weird.wav"), "w").close()
        self.assertEqual(set(voicelib.chimes()), {"weird"})


if __name__ == "__main__":
    unittest.main()
