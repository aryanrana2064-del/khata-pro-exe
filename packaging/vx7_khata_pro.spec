# PyInstaller spec for VX7 KHATA PRO (one-folder build, windowed).
# Build from the project root:  pyinstaller packaging/vx7_khata_pro.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH).parent  # SPECPATH is provided by PyInstaller

# ReportLab imports many modules dynamically (e.g. reportlab.graphics.barcode.code128 via exec),
# which PyInstaller's import scan cannot see. Bundle them all explicitly.
# Piper is bundled too, but only ever started as a separate program (main.py --speak): it is GPL-3 and links
# espeak-ng, so it must not be linked into this proprietary process. Its espeak-ng dictionaries and the
# onnxruntime DLLs are data, not imports, so they have to be collected by hand.
# The Hindi voice (main.py --speak-hindi) runs on onnxruntime and sentencepiece, which are permissive, but it is
# still started as its own program so a model fault cannot reach the ledger.
hidden = (collect_submodules("reportlab") + collect_submodules("openpyxl")
          + collect_submodules("piper") + collect_submodules("onnxruntime")
          + collect_submodules("sentencepiece"))

datas = ([(str(root / "vx7khata" / "assets"), "vx7khata/assets")]  # bundled fonts, logo, welcome image, voice models
         + collect_data_files("piper") + collect_data_files("onnxruntime")
         + collect_data_files("sentencepiece", excludes=["**/*.cc"]))

binaries = (collect_dynamic_libs("piper") + collect_dynamic_libs("onnxruntime")
            + collect_dynamic_libs("sentencepiece"))

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    datas=datas,
    binaries=binaries,
    hiddenimports=hidden,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VX7 KHATA PRO",
    icon=str(root / "packaging" / "installer" / "app.ico"),
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="VX7 KHATA PRO")
