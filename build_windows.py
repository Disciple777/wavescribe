"""
WaveScribe — Windows Build Script

Builds a standalone executable using PyInstaller, then optionally
creates an installer with Inno Setup.

The build also pre-downloads the Whisper "base" model and bundles it
with the executable so users can use Local mode immediately.

Usage:
    python build_windows.py          # Build executable only
    python build_windows.py --zip     # Build exe + create portable ZIP
    python build_windows.py --installer  # Build exe + Inno Setup installer
"""

import os
import shutil
import subprocess
import sys
import json

# ── Paths ──
ROOT = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(ROOT, "dist")
BUILD_DIR = os.path.join(ROOT, "build")
SPEC_FILE = os.path.join(ROOT, "WaveScribe.spec")
APP_NAME = "WaveScribe"


def pre_download_model() -> str:
    """Pre-download the Whisper base model so it can be bundled.

    The model is cached at ~/.cache/whisper/base.pt (~139 MB).
    After downloading, returns the path to the cached model file.

    If the model is already cached, this is nearly instant.
    """
    print()
    print("=" * 60)
    print("  Pre-downloading Whisper base model...")
    print("=" * 60)

    try:
        import whisper

        # This downloads the model if not already cached
        model = whisper.load_model("base")
        print(f"  [OK] Model loaded: {model.__class__.__name__}")

        # Find the cached model file
        cache_dir = os.path.expanduser("~/.cache/whisper")
        model_path = os.path.join(cache_dir, "base.pt")
        if os.path.exists(model_path):
            size_mb = os.path.getsize(model_path) / (1024 * 1024)
            print(f"  [OK] Cached model: {model_path} ({size_mb:.1f} MB)")
            return model_path
        else:
            print(f"  [WARN] Model file not found at expected path: {model_path}")
            print("  Continuing without bundled model (will be downloaded on first use).")
            return ""
    except Exception as e:
        print(f"  [WARN] Could not pre-download model: {e}")
        print("  Continuing without bundled model (will be downloaded on first use).")
        return ""


def get_whisper_model_files(model_path: str) -> list:
    """Get the bundled model files to include in PyInstaller datas.

    Returns a list of (source_path, dest_path) tuples.
    """
    if not model_path or not os.path.exists(model_path):
        return []

    model_dir = os.path.dirname(model_path)  # ~/.cache/whisper/
    files = []
    # Bundle all model files in the whisper cache directory
    for f in os.listdir(model_dir):
        fp = os.path.join(model_dir, f)
        if os.path.isfile(fp):
            files.append((fp, os.path.join("whisper_models", f)))
    return files


def clean_build_dirs():
    """Clean previous build artifacts."""
    for d in [DIST_DIR, BUILD_DIR]:
        if os.path.exists(d):
            print(f"  [CLEAN] Removing {d}...")
            shutil.rmtree(d)


def create_spec(model_bundle: list):
    """Write the PyInstaller .spec file for WaveScribe."""
    import customtkinter as ctk
    import whisper as whisper_pkg

    ctk_dir = os.path.dirname(ctk.__file__)
    whisper_dir = os.path.dirname(whisper_pkg.__file__)

    # Build the datas list lines
    datas_lines = [
        f'        (r"{os.path.join(ROOT, "assets")}", "assets"),',
        f'        (r"{os.path.join(ctk_dir, "assets")}", "customtkinter/assets"),',
        # Whisper assets (mel_filters.npz, tiktoken files)
        f'        (r"{os.path.join(whisper_dir, "assets")}", "whisper/assets"),',
    ]
    for src, dst in model_bundle:
        # Use forward slashes in destination path to avoid \b, \n etc escape issues
        dst_posix = dst.replace("\\", "/")
        datas_lines.append(f'        (r"{src}", "{dst_posix}"),')
    datas_str = "\n".join(datas_lines)

    pathex_str = f'r"{ROOT}"'
    icon_str = f'r"{os.path.join(ROOT, "assets", "icon.ico")}"'

    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for WaveScribe — AI-powered dictation for Windows.
"""

import sys
from pathlib import Path

# ── Block 1: Analysis ──────────────────────────────────────────────
a = Analysis(
    ["main.py"],
    pathex=[{pathex_str}],
    binaries=[],
    datas=[
{datas_str}
    ],
    hiddenimports=[
        # Core app modules
        "app",
        "app.audio_capture",
        "app.config",
        "app.formatter",
        "app.gui",
        "app.hotkeys",
        "app.overlay",
        "app.sounds",
        "app.state",
        "app.theme",
        "app.transcriber",
        "app.tray",
        "app.typer",
        # Sound device (CFFI backend)
        "sounddevice",
        "_sounddevice_data",
        # System tray
        "pystray",
        "PIL",
        "PIL._imaging",
        "PIL._imagingtk",
        # Global hotkeys
        "keyboard",
        # Audio processing
        "numpy",
        # Config persistence
        "json",
        # Threading / queue
        "threading",
        "queue",
        # Font registration (Windows GDI)
        "ctypes",
        "ctypes.wintypes",
        # Sound playback (Windows)
        "winsound",
        # Temp directory
        "tempfile",
        # HTTP / API
        "httpx",
        "httpcore",
        "openai",
        # Tkinter extras
        "tkinter",
        "tkinter.font",
        "tkinter.filedialog",
        # Windows system libraries for keyboard/pystray
        "pywintypes",
        "win32api",
        "win32con",
        "win32file",
        "win32gui",
        "win32process",
        # PyAutoGUI / typing
        "pyperclip",
        "pyautogui",
        "pytweening",
        "PyGetWindow",
        "PyMsgBox",
        "pyscreeze",
        # pystray dependency
        "six",
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[
        # Test and dev
        "pytest",
        # Build tools
        "setuptools",
        "pip",
    ],
    noarchive=False,
    optimize=2,
)

# ── Block 2: PYMEDATA ─────────────────────────────────────────────
pyz = PYZ(a.pure)

# ── Block 3: EXE (wrapper loader) ─────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="{APP_NAME}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # No console window — GUI only
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon={icon_str},
)

# ── Block 4: COLLECT (one-folder bundle) ───────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="{APP_NAME}",
)
'''
    with open(SPEC_FILE, "w", encoding="utf-8") as f:
        f.write(spec_content)

    print(f"  [OK] Spec file created: {SPEC_FILE}")


def build_exe():
    """Run PyInstaller with the .spec file."""
    print()
    print("=" * 60)
    print(f"  Building {APP_NAME} executable...")
    print("=" * 60)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        SPEC_FILE,
    ]

    result = subprocess.run(cmd, cwd=ROOT, capture_output=False)
    if result.returncode != 0:
        print(f"\n  [FAIL] Build failed with exit code {result.returncode}")
        sys.exit(1)

    exe_path = os.path.join(DIST_DIR, APP_NAME, f"{APP_NAME}.exe")
    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"\n  [OK] Build complete!")
        print(f"  [DIR] {os.path.join(DIST_DIR, APP_NAME)}/")
        print(f"  [EXE] {APP_NAME}.exe ({size_mb:.1f} MB)")
    else:
        print(f"\n  [FAIL] Executable not found at {exe_path}")
        sys.exit(1)


def check_inno_setup():
    """Check if Inno Setup is available (iscc compiler)."""
    # Check common install paths
    paths = [
        r"C:\Program Files (x86)\Inno Setup 6\iscc.exe",
        r"C:\Program Files\Inno Setup 6\iscc.exe",
        r"C:\Program Files (x86)\Inno Setup 5\iscc.exe",
        r"C:\Program Files\Inno Setup 5\iscc.exe",
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def install_inno_setup():
    """Install Inno Setup via winget."""
    print()
    print("=" * 60)
    print("  Installing Inno Setup...")
    print("=" * 60)

    try:
        result = subprocess.run(
            ["winget", "install", "JRSoftware.InnoSetup", "--accept-source-agreements", "--accept-package-agreements"],
            capture_output=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        print("  [FAIL] winget not available. Please install Inno Setup manually from:")
        print("     https://jrsoftware.org/isdl.php")
        return False


def create_installer_script():
    """Create Inno Setup .iss script for WaveScribe."""
    iss_content = f"""; WaveScribe Installer Script
; Generated by build_windows.py

#define MyAppName "WaveScribe"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "WaveScribe"
#define MyAppURL "https://wavescribe.ai"
#define MyAppExeName "WaveScribe.exe"

[Setup]
AppId={{{{B8F4A3D2-1C5E-4A9B-8D6F-7E2C3A1B5D9F}}}}
AppName={{#MyAppName}}
AppVersion={{#MyAppVersion}}
AppPublisher={{#MyAppPublisher}}
AppPublisherURL={{#MyAppURL}}
AppSupportURL={{#MyAppURL}}
AppUpdatesURL={{#MyAppURL}}
DefaultDirName={{autopf}}\\{{#MyAppName}}
DefaultGroupName={{#MyAppName}}
AllowNoIcons=yes
LicenseFile=
PrivilegesRequired=admin
OutputDir={os.path.join(ROOT, "installer")}
OutputBaseFilename=WaveScribe-Setup-{{#MyAppVersion}}
SetupIconFile={os.path.join(ROOT, "assets", "icon.ico")}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={{app}}\\{{#MyAppExeName}}
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: checkedonce

[Files]
Source: "{os.path.join(DIST_DIR, APP_NAME, "*")}"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"
Name: "{{autodesktop}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "Launch {{#MyAppName}}"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{{app}}\\unins000.exe"; RunOnceId: "DeleteConfig"
"""

    iss_path = os.path.join(ROOT, "installer.iss")
    with open(iss_path, "w", encoding="utf-8") as f:
        f.write(iss_content)

    print(f"  [OK] Installer script created: {iss_path}")
    return iss_path


def build_installer(iscc_path):
    """Run Inno Setup compiler to create the installer."""
    iss_path = os.path.join(ROOT, "installer.iss")
    print()
    print("=" * 60)
    print("  Building installer...")
    print("=" * 60)

    result = subprocess.run(
        [iscc_path, iss_path],
        cwd=ROOT,
        capture_output=False,
    )

    # Check for output
    installer_dir = os.path.join(ROOT, "installer")
    if os.path.exists(installer_dir):
        exes = [f for f in os.listdir(installer_dir) if f.endswith(".exe")]
        if exes:
            print(f"\n  [OK] Installer created!")
            for exe in exes:
                size_mb = os.path.getsize(os.path.join(installer_dir, exe)) / (1024 * 1024)
                print(f"  [FILE] {os.path.join(installer_dir, exe)} ({size_mb:.1f} MB)")


def create_portable_zip():
    """Create a portable ZIP archive of the dist folder."""
    import zipfile

    print()
    print("=" * 60)
    print("  Creating portable ZIP archive...")
    print("=" * 60)

    installer_dir = os.path.join(ROOT, "installer")
    os.makedirs(installer_dir, exist_ok=True)

    zip_path = os.path.join(installer_dir, "WaveScribe-Portable.zip")
    dist_path = os.path.join(DIST_DIR, APP_NAME)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(dist_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.join(APP_NAME, os.path.relpath(file_path, dist_path))
                zf.write(file_path, arcname)

    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"  [OK] ZIP archive: {zip_path} ({size_mb:.1f} MB)")


def main():
    print()
    print("=" * 60)
    print("  WaveScribe Windows Build")
    print("=" * 60)

    make_zip = "--zip" in sys.argv
    make_installer = "--installer" in sys.argv

    # Step 1: Pre-download Whisper model
    print()
    print("--- Step 0: Pre-download Whisper model ---")
    model_path = pre_download_model()
    model_bundle = get_whisper_model_files(model_path)
    if model_bundle:
        print(f"  [OK] Bundling {len(model_bundle)} model file(s)")
    else:
        print("  [INFO] No model to bundle")

    # Step 2: Clean
    print()
    print("--- Step 1: Clean ---")
    clean_build_dirs()

    # Step 3: Create spec
    print()
    print("--- Step 2: Create spec ---")
    create_spec(model_bundle)

    # Step 4: Build executable
    print()
    print("--- Step 3: Build executable ---")
    build_exe()

    # Step 5: Create portable ZIP (if requested)
    if make_zip:
        print()
        print("--- Step 4: Create ZIP ---")
        create_portable_zip()

    if not make_installer:
        if make_zip:
            print()
            print("=" * 60)
            print("  [OK] Executable + ZIP complete!")
            print("=" * 60)
        else:
            print()
            print("=" * 60)
            print("  [OK] Executable build complete!")
            print("  Run with --installer flag to also create an installer.")
            print("=" * 60)
        return

    # Step 6: Install Inno Setup (if needed)
    print()
    print("--- Step 5: Check/Install Inno Setup ---")
    iscc = check_inno_setup()
    if not iscc:
        print("  Inno Setup not found. Installing...")
        if not install_inno_setup():
            print()
            print("  [FAIL] Could not install Inno Setup automatically.")
            print("  Please install it manually from https://jrsoftware.org/isdl.php")
            print("  Then run this script again with --installer")
            return
        iscc = check_inno_setup()
        if not iscc:
            print("  [FAIL] Inno Setup installed but iscc not found in PATH.")
            return

    # Step 7: Create installer script
    print()
    print("--- Step 6: Create installer script ---")
    create_installer_script()

    # Step 8: Build installer
    print()
    print("--- Step 7: Build installer ---")
    build_installer(iscc)

    print()
    print("=" * 60)
    print("  [DONE] All done!")
    print(f"  [DIR] Executable: {os.path.join(DIST_DIR, APP_NAME)}/")
    print(f"  [DIR] Installer: {os.path.join(ROOT, 'installer')}/")
    print("=" * 60)


if __name__ == "__main__":
    main()
