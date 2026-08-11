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


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_none_without_sidecar(self):
        audio = os.path.join(self.dir, "steve.wav")
        open(audio, "w").close()
        self.assertIsNone(voicelib.transcript(audio))

    def test_reads_and_strips_sidecar(self):
        audio = os.path.join(self.dir, "steve.wav")
        open(audio, "w").close()
        with open(os.path.join(self.dir, "steve.txt"), "w", encoding="utf-8") as f:
            f.write("  hello there  \n")
        self.assertEqual(voicelib.transcript(audio), "hello there")


if __name__ == "__main__":
    unittest.main()
