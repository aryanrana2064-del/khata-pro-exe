@echo off
rem Builds VX7 KHATA PRO for Windows: tests -> PyInstaller exe -> (optional) Inno Setup installer.
rem Run from the project root in a normal Command Prompt:  packaging\build_windows.bat
setlocal
cd /d "%~dp0\.."

where py >nul 2>nul || (echo Python launcher "py" not found. Install Python 3.10+ from python.org & exit /b 1)

if not exist .venv (
  py -3 -m venv .venv || exit /b 1
)
call .venv\Scripts\activate.bat || exit /b 1
python -m pip install --upgrade pip || exit /b 1
python -m pip install -r requirements-dev.txt || exit /b 1

echo.
echo === Running tests ===
set QT_QPA_PLATFORM=offscreen
python -m pytest -q tests || (echo Tests failed - not building. & exit /b 1)

echo.
echo === Building executable ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
pyinstaller packaging\vx7_khata_pro.spec --noconfirm --clean || exit /b 1
echo Executable: dist\VX7 KHATA PRO\VX7 KHATA PRO.exe

where ISCC >nul 2>nul
if errorlevel 1 (
  echo.
  echo Inno Setup ^(ISCC^) not found - skipping installer. Install it from https://jrsoftware.org/isinfo.php
  echo then run:  ISCC packaging\installer.iss
  exit /b 0
)
echo.
echo === Building installer ===
for /f "delims=" %%v in ('python -c "import vx7khata; print(vx7khata.__version__)"') do set APPVER=%%v
ISCC /DAppVersion=%APPVER% packaging\installer.iss || exit /b 1
echo Installer: dist\installer\
endlocal
