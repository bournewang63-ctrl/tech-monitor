@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo 第一次執行：建立虛擬環境並安裝套件...
  py -3 -m venv .venv || python -m venv .venv
  call .venv\Scripts\activate.bat
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
echo [1/2] 收集資料（約 1-2 分鐘）...
python collect.py
echo [2/2] 產生儀表板...
python build.py
start "" "site\index.html"
pause
