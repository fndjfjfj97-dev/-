@echo off
cd /d "%~dp0"
set /p CREATOR_PASSWORD=Придумайте пароль для страницы автора: 
if "%CREATOR_PASSWORD%"=="" (
  echo Пароль не задан. Запуск отменён.
  pause
  exit /b 1
)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
python app.py
pause
