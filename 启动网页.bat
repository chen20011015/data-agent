@echo off
chcp 65001 >nul
cd /d "%~dp0"
cls
echo.
echo  ============================================================
echo    Data Analysis Agent  本地网页版
echo.
echo    第一次用：把 API Key 粘贴到下面，回车
echo    以后用：直接关掉窗口重开，Key 会记住
echo  ============================================================
echo.
if exist key.txt (
    set /p API_KEY=<key.txt
) else (
    set /p API_KEY=请粘贴 API Key 后回车:
    echo %API_KEY%>key.txt
    echo Key 已保存到 key.txt（删掉这个文件就会重新询问）
)
echo.
echo  浏览器会自动打开 http://127.0.0.1:8000
echo  用完直接关掉这个黑窗口即可
echo.
python server.py
pause
