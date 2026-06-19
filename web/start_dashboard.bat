@echo off
REM Double-click to run the OpenMontage scene-review dashboard in its own window.
REM It stays up until you close this window (independent of any agent session).
cd /d "%~dp0.."
echo Starting scene-review dashboard at http://localhost:8011  (Ctrl+C or close window to stop)
python -m uvicorn web.backend.app:app --port 8011
echo.
echo Server stopped.
pause
