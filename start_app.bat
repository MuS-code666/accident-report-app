@echo off
rem Start the accident report app (start Ollama first)
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m accident_report.app
