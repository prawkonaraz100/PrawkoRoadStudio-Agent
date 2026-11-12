@echo off
setlocal
"F:\RoadMaker-evaluation\toolchain\Python312\python.exe" "%~dp0roadctl.py" %*
exit /b %ERRORLEVEL%
