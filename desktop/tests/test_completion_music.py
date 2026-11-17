import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from signal_archive import completion_music
from signal_archive.direct_service import DirectService


class CompletionMusicTests(unittest.TestCase):
    def test_picks_audio_from_music_and_opens_without_shell(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            music = home / "Music"
            music.mkdir()
            (music / "notes.txt").write_text("not music")
            song = music / "song with spaces.mp3"
            song.touch()
            with patch.object(completion_music.Path, "home", return_value=home), \
                 patch.object(completion_music.os, "name", "posix"), \
                 patch.object(completion_music.subprocess, "run") as popen:
                completion_music._play()
            self.assertEqual(popen.call_args.args[0], ["xdg-open", str(song)])

    def test_windows_uses_system_music_player(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            music = home / "Music"
            music.mkdir()
            song = music / "song.wav"
            song.touch()
            with patch.object(completion_music.Path, "home", return_value=home), \
                 patch.object(completion_music.os, "startfile", create=True) as startfile, \
                 patch.object(completion_music.os, "name", "nt"):
                completion_music._play()
            startfile.assert_called_once_with(str(song))

    def test_missing_music_and_player_are_harmless(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with patch.object(completion_music.Path, "home", return_value=home):
                completion_music._play()
            (home / "Music").mkdir()
            (home / "Music" / "song.mp3").touch()
            with patch.object(completion_music.Path, "home", return_value=home), \
                 patch.object(completion_music.os, "name", "posix"), \
                 patch.object(completion_music.subprocess, "run", side_effect=OSError("missing player")):
                completion_music._play()

    def test_export_success_does_not_depend_on_playback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = DirectService(None, root / "state", root / "exports")
            async def operation():
                return {"ok": True}
            with patch.object(completion_music, "play_on_completion", side_effect=RuntimeError("no desktop")):
                result = asyncio.run(service._run("export messages", operation))
            self.assertEqual(result, {"ok": True})
            self.assertEqual(service.state["job"]["status"], "success")


if __name__ == "__main__":
    unittest.main()
