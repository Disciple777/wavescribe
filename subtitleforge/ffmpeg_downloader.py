"""Auto-download and manage a portable ffmpeg binary for Windows.

If ffmpeg is not installed on the system PATH, this module will
download the latest portable "essentials" build from gyan.dev
(authoritative Windows ffmpeg builds) and cache it in a local
``ffmpeg_bin/`` directory within the SubtitleForge package.

The download happens once and the binary is reused on subsequent runs.
"""

import io
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Callable, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

# ── Download Configuration ──

# Official static URL for the latest Windows ffmpeg essentials zip
FFMPEG_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

# How long to wait for the download (seconds)
DOWNLOAD_TIMEOUT = 120

# Where we store ffmpeg locally (relative to this file)
FFMPEG_BIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg_bin")

# Expected executable names
FFMPEG_EXE = "ffmpeg.exe"
FFPROBE_EXE = "ffprobe.exe"


class FfmpegDownloadError(Exception):
    """Raised when ffmpeg download or extraction fails."""
    pass


def _get_local_ffmpeg_path() -> Optional[str]:
    """Check if ffmpeg.exe exists in our local ``ffmpeg_bin/`` directory.

    Returns:
        The full path to ffmpeg.exe, or None if not found.
    """
    path = os.path.join(FFMPEG_BIN_DIR, FFMPEG_EXE)
    return path if os.path.exists(path) else None


def _get_local_ffprobe_path() -> Optional[str]:
    """Check if ffprobe.exe exists in our local ``ffmpeg_bin/`` directory."""
    path = os.path.join(FFMPEG_BIN_DIR, FFPROBE_EXE)
    return path if os.path.exists(path) else None


def is_ffmpeg_available() -> bool:
    """Check if ffmpeg is available (either on PATH or in local cache).

    Returns:
        True if ffmpeg is usable, False otherwise.
    """
    # Check local cache first
    if _get_local_ffmpeg_path():
        return True
    # Check system PATH
    return shutil.which("ffmpeg") is not None


def get_ffmpeg_path() -> Optional[str]:
    """Get the path to ffmpeg, downloading it if needed.

    Priority:
      1. Local ``ffmpeg_bin/`` cache
      2. System PATH
      3. Auto-download to local cache (if not found)

    Returns:
        Full path to ffmpeg.exe, or None if download failed.
    """
    # 1. Local cache
    local = _get_local_ffmpeg_path()
    if local:
        return local

    # 2. System PATH
    system = shutil.which("ffmpeg")
    if system:
        return system

    # 3. Auto-download
    try:
        return _download_and_cache()
    except FfmpegDownloadError:
        return None


def get_ffprobe_path() -> Optional[str]:
    """Get the path to ffprobe, downloading ffmpeg if needed."""
    local = _get_local_ffprobe_path()
    if local:
        return local

    system = shutil.which("ffprobe")
    if system:
        return system

    # If ffmpeg was downloaded, ffprobe should be alongside it
    if _get_local_ffmpeg_path():
        return _get_local_ffprobe_path()

    return None


def ensure_ffmpeg(progress_callback: Optional[Callable[[float, str], None]] = None) -> bool:
    """Ensure ffmpeg is available, downloading it if necessary.

    This is the main entry point. Call it once at startup or before
    attempting audio extraction.

    Args:
        progress_callback: Optional function called with (progress_0to1, status_text).
                           Can be used to update a progress bar in the GUI.

    Returns:
        True if ffmpeg is now available, False if it couldn't be obtained.
    """
    # Already available?
    if _get_local_ffmpeg_path() or shutil.which("ffmpeg"):
        if progress_callback:
            progress_callback(1.0, "ffmpeg is available")
        return True

    # Need to download
    if progress_callback:
        progress_callback(0.0, "Downloading ffmpeg... (55 MB)")

    try:
        path = _download_and_cache(progress_callback)
        if path:
            if progress_callback:
                progress_callback(1.0, "ffmpeg ready!")
            return True
    except FfmpegDownloadError as e:
        if progress_callback:
            progress_callback(0.0, f"Download failed: {e}")
        return False

    return False


def _download_and_cache(
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> str:
    """Download the latest ffmpeg Windows build and extract it locally.

    Downloads the essentials zip from gyan.dev, extracts ``ffmpeg.exe``
    and ``ffprobe.exe``, and saves them in ``ffmpeg_bin/``.

    Returns:
        Path to the cached ffmpeg.exe.

    Raises:
        FfmpegDownloadError: If download or extraction fails.
    """
    # Create local bin directory
    os.makedirs(FFMPEG_BIN_DIR, exist_ok=True)

    if progress_callback:
        progress_callback(0.1, "Connecting to gyan.dev...")

    try:
        # Open connection with timeout
        req = Request(FFMPEG_DOWNLOAD_URL, headers={"User-Agent": "SubtitleForge/1.0"})
        response = urlopen(req, timeout=DOWNLOAD_TIMEOUT)

        # Get total file size for progress reporting
        total_size = response.length
        downloaded = 0
        chunk_size = 8192

        # Download to memory (zip file is ~55 MB)
        if progress_callback:
            progress_callback(0.15, "Downloading ffmpeg... (0%)")

        data_chunks = []
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            data_chunks.append(chunk)
            downloaded += len(chunk)

            if total_size and progress_callback:
                # Scale progress: 15% to 70% of the overall process
                pct = 0.15 + (downloaded / total_size) * 0.55
                pct_display = int(100 * downloaded / total_size) if total_size else downloaded // (1024 * 1024)
                progress_callback(
                    pct,
                    f"Downloading ffmpeg... ({pct_display}%  •  {downloaded // (1024*1024)} MB / {total_size // (1024*1024)} MB)",
                )

        zip_data = b"".join(data_chunks)

        if not zip_data:
            raise FfmpegDownloadError("Downloaded file is empty.")

        if progress_callback:
            progress_callback(0.72, "Extracting ffmpeg.exe...")

        # Extract the zip
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            # Find ffmpeg.exe and ffprobe.exe in the zip structure
            # The zip contains a folder like "ffmpeg-7.0-essentials_build/bin/ffmpeg.exe"
            ffmpeg_entry = None
            ffprobe_entry = None

            for name in zf.namelist():
                # Normalize path separators
                normalized = name.replace("\\", "/")
                if normalized.endswith("/bin/ffmpeg.exe") and ffmpeg_entry is None:
                    ffmpeg_entry = name
                elif normalized.endswith("/bin/ffprobe.exe") and ffprobe_entry is None:
                    ffprobe_entry = name

            # Extract ffmpeg.exe
            if ffmpeg_entry:
                with zf.open(ffmpeg_entry) as source:
                    with open(os.path.join(FFMPEG_BIN_DIR, FFMPEG_EXE), "wb") as dest:
                        shutil.copyfileobj(source, dest)

                # Make executable (Windows doesn't need chmod, but good practice)
                ffmpeg_path = os.path.join(FFMPEG_BIN_DIR, FFMPEG_EXE)
                try:
                    os.chmod(ffmpeg_path, 0o755)
                except OSError:
                    pass
            else:
                raise FfmpegDownloadError(
                    "Could not find ffmpeg.exe in the downloaded archive."
                )

            # Extract ffprobe.exe (for duration detection)
            if ffprobe_entry:
                with zf.open(ffprobe_entry) as source:
                    with open(os.path.join(FFMPEG_BIN_DIR, FFPROBE_EXE), "wb") as dest:
                        shutil.copyfileobj(source, dest)
                try:
                    os.chmod(os.path.join(FFMPEG_BIN_DIR, FFPROBE_EXE), 0o755)
                except OSError:
                    pass

        # Verify the extracted executable
        if not os.path.exists(os.path.join(FFMPEG_BIN_DIR, FFMPEG_EXE)):
            raise FfmpegDownloadError("Extraction completed but ffmpeg.exe not found.")

        size_mb = os.path.getsize(os.path.join(FFMPEG_BIN_DIR, FFMPEG_EXE)) / (1024 * 1024)

        if progress_callback:
            progress_callback(0.95, f"Verifying... ({size_mb:.1f} MB)")

        return os.path.join(FFMPEG_BIN_DIR, FFMPEG_EXE)

    except URLError as e:
        raise FfmpegDownloadError(
            f"Could not download ffmpeg from:\n"
            f"  {FFMPEG_DOWNLOAD_URL}\n\n"
            f"Network error: {e.reason}\n\n"
            f"Please download ffmpeg manually from:\n"
            f"  https://ffmpeg.org/download.html"
        )
    except zipfile.BadZipFile:
        raise FfmpegDownloadError(
            "Downloaded file is corrupted (not a valid zip). "
            "Please try again or download ffmpeg manually."
        )
    except OSError as e:
        raise FfmpegDownloadError(f"File system error: {e}")


def get_local_ffmpeg_size() -> str:
    """Return a human-readable string of the local ffmpeg file size."""
    path = _get_local_ffmpeg_path()
    if not path:
        return "Not cached"
    try:
        size_bytes = os.path.getsize(path)
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    except OSError:
        return "Unknown"
