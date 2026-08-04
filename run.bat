@echo off
cd /d %~dp0
if exist config.toml (
  .venv\Scripts\python src\coverage_analysis.py --config config.toml %*
) else (
  .venv\Scripts\python src\coverage_analysis.py %*
)
