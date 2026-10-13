"""Baut die Windows-EXE (eine Datei, ohne Konsole).

Aufruf (am besten in einer frischen venv mit requirements.txt + pyinstaller):
    python scripts/build_exe.py
Ergebnis: release/UebungMerger-<Version>-windows.exe
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from uebung_merger import __version__  # noqa: E402

NAME = "UebungMerger"
UNUSED_QT = [
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel",
    "QtQml", "QtQuick", "QtQuickWidgets", "QtQuick3D", "Qt3DCore", "Qt3DRender",
    "QtMultimedia", "QtMultimediaWidgets", "QtPdf", "QtPdfWidgets", "QtCharts",
    "QtDataVisualization", "QtSql", "QtNetwork", "QtBluetooth", "QtNfc",
    "QtPositioning", "QtSensors", "QtSerialPort", "QtSvg", "QtSvgWidgets",
    "QtTest", "QtOpenGL", "QtOpenGLWidgets", "QtDesigner", "QtHelp", "QtScxml",
]
UNUSED_PY = ["tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest"]


def version_file() -> Path:
    """Windows-Versionsressource, damit die EXE im Explorer Name und Version zeigt."""
    parts = (__version__.split(".") + ["0", "0", "0"])[:3]
    tup = ", ".join(parts + ["0"])
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040704B0', [
      StringStruct('CompanyName', 'Simon Pablo Heppner'),
      StringStruct('FileDescription', 'Übung Merger'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{NAME}'),
      StringStruct('LegalCopyright', 'AGPL-3.0'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', 'Übung Merger'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1031, 1200])])
  ])
"""
    path = ROOT / "build" / "version_info.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def main() -> None:
    assets = ROOT / "uebung_merger" / "assets"
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed", "--name", NAME,
        "--icon", str(assets / "icon.ico"),
        "--version-file", str(version_file()),
        "--add-data", f"{assets};uebung_merger/assets",
        "--distpath", str(ROOT / "build" / "dist"),
        "--workpath", str(ROOT / "build" / "work"),
        "--specpath", str(ROOT / "build"),
    ]
    for mod in UNUSED_QT:
        cmd += ["--exclude-module", f"PySide6.{mod}"]
    for mod in UNUSED_PY:
        cmd += ["--exclude-module", mod]
    cmd.append(str(ROOT / "scripts" / "launch.py"))
    subprocess.run(cmd, check=True, cwd=ROOT)

    out = ROOT / "release"
    out.mkdir(exist_ok=True)
    target = out / f"{NAME}-{__version__}-windows.exe"
    shutil.copy2(ROOT / "build" / "dist" / f"{NAME}.exe", target)
    print(f"\nFertig: {target} ({target.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
