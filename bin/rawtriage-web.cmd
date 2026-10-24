@echo off
rem rawtriage-web.cmd: the Windows twin of bin/rawtriage-web. Starts (or opens) the RawNotes triage website: server.py
rem from this checkout, every flag passed on. Uses the py launcher when it is there, else python on PATH; never
rem python3, which on Windows is usually the Microsoft Store stub that only prints "Python was not found".
setlocal
set "repo=%~dp0.."
where py >nul 2>nul && (py -3 "%repo%\server.py" %* & exit /b)
python "%repo%\server.py" %*
