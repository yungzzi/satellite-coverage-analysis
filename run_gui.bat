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
.venv\Scripts\streamlit run src\loader_app.py
