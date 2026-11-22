@echo off
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
python -m expanded_garage %*
exit /b %ERRORLEVEL%
