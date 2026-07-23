@echo off
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
start "生产运营指挥看板" "%EDGE%" --kiosk "http://192.168.31.100:8090/" --edge-kiosk-type=fullscreen --no-first-run
