@echo off
setlocal enabledelayedexpansion
title TIMDR EV Predict - Dashboard

echo ============================================================
echo  TIMDR EV Predict - uruchamianie dashboardu
echo ============================================================
echo.

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    set PYCMD=python
) else (
    where py >nul 2>nul
    if %ERRORLEVEL%==0 (
        set PYCMD=py
    ) else (
        echo [BLAD] Nie znaleziono Pythona w PATH.
        echo Pobierz z https://www.python.org/downloads/
        echo Przy instalacji zaznacz "Add python.exe to PATH".
        pause
        exit /b 1
    )
)

echo Uzywam: %PYCMD%
echo.
echo Instaluje/aktualizuje zaleznosci (flask, numpy, scipy)...
%PYCMD% -m pip install --quiet --upgrade pip
%PYCMD% -m pip install --quiet flask numpy scipy
if %ERRORLEVEL% NEQ 0 (
    echo [BLAD] Instalacja zaleznosci nie powiodla sie.
    pause
    exit /b 1
)

echo.
echo Uruchamiam serwer API + dashboard na http://127.0.0.1:5000 ...
echo (zamknij to okno, zeby zatrzymac serwer)
echo.

start "" http://127.0.0.1:5000
%PYCMD% api.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [BLAD] Serwer zakonczyl sie bledem - tresc bledu powyzej.
    pause
)
