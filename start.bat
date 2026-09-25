@echo off
chcp 65001 > nul
title JobAssistent + Ollama

echo [1/3] Очистка видеопамяти и остановка модели...
ollama stop qwen2.5:14b-instruct >nul 2>&1
taskkill /f /im ollama_llama_server.exe >nul 2>&1
taskkill /f /im ollama.exe >nul 2>&1

echo [2/3] Запуск фонового сервера Ollama...
start /min "" ollama serve

timeout /t 2 /nobreak > nul

echo [3/3] Запуск Карьерного ассистента (Streamlit)...
echo.
echo Закройте это окно или нажмите Ctrl+C — сервер Ollama тоже остановится.
echo.

REM Запускаем Streamlit и ждём его завершения
python -m streamlit run app.py

REM === Этот блок выполнится, когда Streamlit закроется ===
echo.
echo [Очистка] Останавливаю Ollama и освобождаю видеопамять...
ollama stop qwen2.5:14b-instruct >nul 2>&1
taskkill /f /im ollama_llama_server.exe >nul 2>&1
taskkill /f /im ollama.exe >nul 2>&1
echo Готово. Можно закрывать окно.
timeout /t 2 >nul