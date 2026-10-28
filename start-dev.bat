@echo off

REM Starts both servers from the project folder
cd /d "%~dp0"

REM Starts the Python backend
start "Engine Tuning Backend" cmd /k "py -m uvicorn backend.main:app --reload"

REM Starts the React frontend
start "Engine Tuning Frontend" cmd /k "cd frontend && npm.cmd run dev"