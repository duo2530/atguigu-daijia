@echo off
rem Load project-root .env into env vars for java. Called by start-all.ps1 before java starts.
rem Processes created via WMI do not inherit PowerShell env vars, so it must be done here. Never prints values.
set "ENVFILE=%~dp0..\.env"
if not exist "%ENVFILE%" exit /b 0
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENVFILE%") do (
  if /i "%%A"=="APP_ID" set "WX_APP_ID=%%B"
  if /i "%%A"=="APP_SECRET" set "WX_APP_SECRET=%%B"
  if /i "%%A"=="WX_PAY_MCH_ID" set "WX_PAY_MCH_ID=%%B"
  if /i "%%A"=="WX_PAY_SERIAL_NO" set "WX_PAY_SERIAL_NO=%%B"
  if /i "%%A"=="WX_PAY_API_V3_KEY" set "WX_PAY_API_V3_KEY=%%B"
  if /i "%%A"=="WX_PAY_PRIVATE_KEY_PATH" set "WX_PAY_PRIVATE_KEY_PATH=%%B"
  if /i "%%A"=="WX_PAY_PUBLIC_KEY_PATH" set "WX_PAY_PUBLIC_KEY_PATH=%%B"
  if /i "%%A"=="WX_PAY_PUBLIC_KEY_ID" set "WX_PAY_PUBLIC_KEY_ID=%%B"
  if /i "%%A"=="TENCENT_CLOUD_SECRET_ID" set "TENCENT_CLOUD_SECRET_ID=%%B"
  if /i "%%A"=="TENCENT_CLOUD_SECRET_KEY" set "TENCENT_CLOUD_SECRET_KEY=%%B"
)
exit /b 0
