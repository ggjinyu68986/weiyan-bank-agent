@echo off
chcp 65001 >nul
echo ============================================
echo  微言 AI 银行助手 - 一键重启后端 (8000)
echo ============================================
echo [1/3] 关闭占用 8000 端口的旧进程...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo    正在结束进程 PID %%a ...
    taskkill /f /pid %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul
echo [2/3] 启动最新代码后端...
cd /d D:\Desktop\weiyan-bank-agent
start "weiyan-backend" python -m uvicorn backend.api.main:app --port 8000
timeout /t 5 /nobreak >nul
echo [3/3] 检查是否启动成功...
netstat -ano | findstr ":8000" | findstr "LISTENING"
echo.
echo 若上方出现 LISTENING 行，说明启动成功，请刷新浏览器页面。
echo 若提示权限不足或端口仍被占用，请先手动关掉正在运行的 uvicorn 窗口再双击本脚本。
pause
