@echo off
title NATIONAL BORDER DEFENSE - TACTICAL COMMAND DASHBOARD
color 0A
cls
echo ======================================================================
echo    NATIONAL BORDER RF SIGINT & TARGET GEOLOCATION RADAR SYSTEM
echo ======================================================================
echo.
echo [1] Initializing COM3 (Turret Unit) & COM4 (Ground Station HUD)...
echo [2] Starting Real-Time Tactical Web Dashboard Server on port 5000...
echo.
echo [3] Web Command Dashboard URLs:
echo       - On this Laptop:   http://localhost:5000
echo.
echo [4] Opening Web Browser in 2 seconds...
echo ======================================================================
timeout /t 2 /nobreak > nul
start http://localhost:5000
"C:\Users\erfut\.platformio\penv\Scripts\python.exe" -u "%~dp0tactical_dashboard_server.py"
pause
