@echo off
cd /d %~dp0
echo ============================================================
echo   卫星覆盖分析工作台
echo   启动后浏览器自动打开（或手动访问 http://localhost:8501）
echo.
echo   提示：
echo    - 关闭浏览器标签页【不会】停止程序，重新打开上面的网址即可回来；
echo    - 彻底停止：点界面左侧"停止并退出程序"按钮，或按 Ctrl+C。
echo ============================================================
set "VENV=.venv"
if not exist "%VENV%\Scripts\streamlit.exe" if exist "..\卫星覆盖性分析\.venv\Scripts\streamlit.exe" set "VENV=..\卫星覆盖性分析\.venv"
if not exist "%VENV%\Scripts\streamlit.exe" (
  echo [ERROR] Python env not found: .venv
  echo 本工作树没有安装 Python 依赖；可到主工程工作树运行，或在本目录执行：
  echo   python -m venv .venv
  echo   .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)
"%VENV%\Scripts\streamlit" run src\loader_app.py
