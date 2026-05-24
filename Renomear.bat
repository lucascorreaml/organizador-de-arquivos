@echo off
chcp 65001 >nul
title Renomeador de Arquivos e Pastas
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py "%~dp0renomear.py"
) else (
    python "%~dp0renomear.py"
)

echo.
echo App encerrado. Pode fechar esta janela.
pause >nul
