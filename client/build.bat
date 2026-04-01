@echo off
echo =============================================
echo  Study Tracker - EXE Build
echo =============================================

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install from https://python.org
    echo Make sure to check "Add Python to PATH"
    pause
    exit /b
)

echo Installing packages...
python -m pip install pynput requests pyinstaller plyer

echo.
echo Building EXE...
python -m PyInstaller --onefile --windowed --name StudyTracker ^
  --hidden-import=requests ^
  --hidden-import=pynput ^
  --hidden-import=pynput.keyboard ^
  --hidden-import=pynput.keyboard._win32 ^
  --hidden-import=pynput.mouse ^
  --hidden-import=pynput.mouse._win32 ^
  client.py

echo.
echo Done! Check dist\StudyTracker.exe
pause
