@echo off
cd /d "%~dp0"
rem 真白语音引擎（GPT-SoVITS api_v2）：端口 9880 未开才拉起，避免重复启动
netstat -ano | findstr ":9880" | findstr "LISTENING" >nul
if errorlevel 1 (
    start "MaidTTSApi" /min cmd /c "cd /d %~dp0gptsovits\GPT-SoVITS-v2pro-20250604 && runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS\configs\tts_infer.yaml"
    timeout /t 6 /nobreak >nul
)
start "SakuraMaid" pyw main.py
