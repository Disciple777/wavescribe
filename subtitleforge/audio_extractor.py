"""Audio extraction from video files using ffmpeg.

Automatically detects ffmpeg and downloads a portable copy if not
found on the system. Extracts audio as 16-bit 16 kHz mono WAV
for Whisper compatibility.
"""

import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, Optional, Tuple

from subtitleforge.ffmpeg_downloader import (
    ensure_ffmpeg as _ensure_ffmpeg,
    get_ffmpeg_path as _get_cached_ffmpeg_path,
    get_ffprobe_path as _get_cached_ffprobe_path,
    is_ffmpeg_available as _cached_ffmpeg_available,
)


class AudioExtractionError(Exception):
    """Raised when audio extraction fails."""
    pass


def is_ffmpeg_available() -> bool:
    """Check if ffmpeg is available (system PATH or local cache).

    Does NOT trigger a download — use ``ensure_ffmpeg()`` for that.
    """
    return _cached_ffmpeg_available()


def get_ffmpeg_path() -> Optional[str]:
    """Return the path to the ffmpeg executable, downloading if needed.

    Returns:
        Full path to ffmpeg.exe, or None if unavailable.
    """
    return _get_cached_ffmpeg_path()


def ensure_ffmpeg(
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> bool:
    """Ensure ffmpeg is available, auto-downloading if necessary.

    Call this before attempting audio extraction to guarantee ffmpeg
    is ready. Shows download progress via the optional callback.

    Args:
        progress_callback: ``fn(progress_0to1, status_text)`` for UI updates.

    Returns:
        True if ffmpeg is ready, False if it couldn't be obtained.
    """
    return _ensure_ffmpeg(progress_callback=progress_callback)


def get_video_duration(video_path: str) -> Optional[float]:
    """Get video duration in seconds using ffprobe (or ffmpeg).

    Returns:
        Duration in seconds, or None if it could not be determined.
    """
    if not os.path.exists(video_path):
        return None

    # Prefer ffprobe if available (faster for metadata)
    ffprobe = _get_cached_ffprobe_path() or shutil.which("ffprobe")
    probe_cmd = ffprobe or _get_cached_ffmpeg_path() or shutil.which("ffmpeg")

    if not probe_cmd:
        return None

    try:
        cmd = [
            probe_cmd, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass

    return None


def get_video_info(video_path: str) -> dict:
    """Get basic info about a video file.

    Returns a dict with keys:
      - path: str
      - filename: str
      - size_mb: float (file size)
      - duration_sec: Optional[float]
      - duration_str: str (formatted H:MM:SS)
    """
    info = {
        "path": video_path,
        "filename": os.path.basename(video_path),
        "size_mb": 0.0,
        "duration_sec": None,
        "duration_str": "--:--:--",
    }

    if not os.path.exists(video_path):
        return info

    # File size
    info["size_mb"] = os.path.getsize(video_path) / (1024 * 1024)

    # Duration
    duration = get_video_duration(video_path)
    if duration is not None:
        info["duration_sec"] = duration
        hours = int(duration // 3600)
        minutes = int((duration % 3600) // 60)
        seconds = int(duration % 60)
        info["duration_str"] = f"{hours}:{minutes:02d}:{seconds:02d}"

    return info


SUPPORTED_FORMATS = [
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv",
    ".webm", ".m4v", ".mpg", ".mpeg", ".ts", ".3gp",
]


def is_supported_video(path: str) -> bool:
    """Check if the file extension is a supported video format."""
    ext = os.path.splitext(path)[1].lower()
    return ext in SUPPORTED_FORMATS


def extract_audio_to_wav(video_path: str) -> bytes:
    """Extract audio from a video file as 16-bit 16 kHz mono WAV bytes.

    Uses ffmpeg subprocess to decode the audio stream and re-encode
    as PCM signed 16-bit little-endian at 16000 Hz sample rate (mono),
    which is the format expected by the Whisper API.

    Args:
        video_path: Path to the video file.

    Returns:
        Raw WAV file contents as bytes.

    Raises:
        AudioExtractionError: If ffmpeg is not found or extraction fails.
    """
    if not os.path.exists(video_path):
        raise AudioExtractionError(f"Video file not found: {video_path}")

    if not is_supported_video(video_path):
        raise AudioExtractionError(
            f"Unsupported video format: {os.path.splitext(video_path)[1]}. "
            f"Supported formats: {', '.join(SUPPORTED_FORMATS)}"
        )

    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise AudioExtractionError(
            "ffmpeg is not available.\n\n"
            "SubtitleForge tried to download it automatically, but it failed.\n"
            "Please download ffmpeg manually from:\n"
            "  https://ffmpeg.org/download.html"
        )

    # Create a temporary output file
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(tmp_fd)

    try:
        cmd = [
            ffmpeg,
            "-i", video_path,
            "-vn",                     # no video stream
            "-acodec", "pcm_s16le",    # PCM 16-bit signed little-endian
            "-ar", "16000",            # 16 kHz sample rate
            "-ac", "1",                # mono
            "-y",                      # overwrite output file
            tmp_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute max for long videos
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or "Unknown ffmpeg error"
            # Truncate excessively long error messages
            if len(error_msg) > 500:
                error_msg = error_msg[:500] + "..."
            raise AudioExtractionError(f"ffmpeg error: {error_msg}")

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise AudioExtractionError(
                "ffmpeg produced an empty audio file. "
                "The video may have no audio track."
            )

        with open(tmp_path, "rb") as f:
            wav_bytes = f.read()

        return wav_bytes

    except subprocess.TimeoutExpired:
        raise AudioExtractionError(
            "Audio extraction timed out. The video may be too long."
        )
    except OSError as e:
        raise AudioExtractionError(f"System error during extraction: {e}")
    finally:
        # Clean up temp file
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
