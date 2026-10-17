"""Offline TTS using Windows' built-in voices (System.Speech via PowerShell).

No third-party Python packages are required — the .NET ``System.Speech``
assembly ships with Windows, and voices are whatever the OS has installed
(e.g. Zira / Sabina, or the newer "natural" voices users can add from
Windows Settings → Time & Language → Speech).

Speech happens inside a short-lived PowerShell process, so stopping is as
simple as killing that process. Text is streamed over stdin as UTF-8 to
avoid command-line length and quoting problems.
"""

import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from typing import List, Optional

from app.tts_errors import TTSError


@dataclass
class VoiceInfo:
    """Description of one selectable voice."""

    name: str
    gender: str = ""
    language: str = ""


# ── Embedded PowerShell scripts (materialised to temp on first use) ──

_SPEAK_SCRIPT = r"""param([string]$Voice = '', [int]$Rate = 0, [int]$Volume = 100)
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = $Rate
$synth.Volume = $Volume
if ($Voice) {
    try { $synth.SelectVoice($Voice) } catch { }
}
$text = [Console]::In.ReadToEnd()
if ($text) { $synth.Speak($text) }
$synth.Dispose()
"""

_LIST_SCRIPT = r"""Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($v in $synth.GetInstalledVoices()) {
    if ($v.Enabled) {
        '{0}|{1}' -f $v.VoiceInfo.Name, $v.VoiceInfo.Gender
    }
}
$synth.Dispose()
"""


def _powershell_exe() -> str:
    """Full path to the Windows PowerShell executable."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(
        system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
    )


def _script_path(name: str, content: str) -> str:
    """Materialise a PowerShell script into the temp dir (once)."""
    path = os.path.join(tempfile.gettempdir(), f"wavescribe_{name}.ps1")
    try:
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
    except OSError:
        pass
    return path


def system_rate(rate_percent: int) -> int:
    """Map a 50..150 speed percentage to System.Speech's -10..+10 scale."""
    return round((rate_percent - 100) / 10)


def parse_voices_output(output: str) -> List[VoiceInfo]:
    """Parse the ``Name|Gender`` lines emitted by the voice-list script."""
    voices: List[VoiceInfo] = []
    for line in (output or "").splitlines():
        line = line.strip()
        if not line:
            continue
        name, sep, gender = line.partition("|")
        name = name.strip()
        if sep and name:
            voices.append(VoiceInfo(name=name, gender=gender.strip()))
    return voices


class SystemTTSEngine:
    """Speaks text with the Windows built-in voices (offline, zero deps)."""

    name = "system"
    display_name = "Windows voices (offline)"

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    # ── Voices ──

    def list_voices(self) -> List[VoiceInfo]:
        """Return the voices currently installed on Windows."""
        script = _script_path("list_voices", _LIST_SCRIPT)
        out = self._run(script, [], timeout=15)
        return parse_voices_output(out)

    # ── Speaking ──

    def speak(
        self,
        text: str,
        voice: str = "",
        rate_percent: int = 100,
        volume: int = 100,
        on_done=None,
    ) -> None:
        """Speak ``text`` aloud. Blocks until finished — call from a worker thread."""
        self.stop()

        script = _script_path("speak", _SPEAK_SCRIPT)
        args = ["-Rate", str(system_rate(rate_percent)), "-Volume", str(max(0, min(100, volume)))]
        if voice:
            args = ["-Voice", voice] + args

        cmd = [
            _powershell_exe(),
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", script,
            *args,
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise TTSError(f"Could not start Windows speech engine: {exc}") from exc

        with self._lock:
            self._proc = proc

        try:
            proc.communicate(input=text.encode("utf-8"))
        finally:
            with self._lock:
                # Only clear if we still own the reference — a newer speak()
                # may have replaced it after a stop() killed this process.
                if self._proc is proc:
                    self._proc = None

        if on_done:
            try:
                on_done()
            except Exception:
                pass

    def stop(self) -> None:
        """Stop speaking immediately (kills the speech process)."""
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass

    def is_speaking(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    # ── Internal ──

    @staticmethod
    def _run(script: str, args: List[str], timeout: float = 10.0) -> Optional[str]:
        """Run a PowerShell script file and return its stdout (or None)."""
        cmd = [_powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, *args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", errors="replace")
