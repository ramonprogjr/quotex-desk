@echo off
cd /d "%~dp0"
if not exist frontend\dist\index.html (
  echo Compilando o painel...
  pushd frontend && call npm install && call npm run build && popd
)
start "" http://127.0.0.1:8000
.venv\Scripts\uvicorn backend.api:app --host 127.0.0.1 --port 8000
