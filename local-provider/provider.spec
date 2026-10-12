from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH)
a = Analysis(
    [str(root / "run.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "ui" / "dist"), "ui/dist"),
        (str(root / "app" / "wan_experience_tasks.json"), "app"),
    ],
    hiddenimports=collect_submodules("uvicorn") + ["win32crypt", "win32api", "win32job", "socks"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "numpy", "pandas", "matplotlib", "PyQt5", "PyQt6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="zaowu-provider", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="zaowu-provider")
