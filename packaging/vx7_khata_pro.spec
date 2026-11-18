# PyInstaller spec for VX7 KHATA PRO (one-folder build, windowed).
# Build from the project root:  pyinstaller packaging/vx7_khata_pro.spec --noconfirm
import shutil
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH).parent  # SPECPATH is provided by PyInstaller

# Phone Remote ships its own Node.js runtime so a client PC needs nothing installed.
runtime_dir = root / "vx7khata" / "remote" / "runtime"
node_exe = runtime_dir / "node.exe"
if not node_exe.is_file():
    found_node = shutil.which("node")
    if found_node:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(found_node, node_exe)

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
         + [(str(root / "vx7khata" / "remote"), "vx7khata/remote")]  # Phone Remote backend (JS + win-agent.exe + APK)
         + ([(str(runtime_dir), "vx7khata/remote/runtime")] if node_exe.is_file() else [])
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
