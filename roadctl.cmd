@echo off
setlocal
"%~dp0..\reference\RoadMaker\toolchain\Python312\python.exe" "%~dp0roadctl.py" %*
exit /b %ERRORLEVEL%
