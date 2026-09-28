@echo off
chcp 949 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title CSV 딥러닝 웹앱 - 시작 CPU 모드

echo =====================================================
echo  CSV 딥러닝 웹앱 시작 - CPU 모드
echo =====================================================
echo.

if not exist ".env" (
    echo [오류] .env 파일이 없습니다.
    echo .env.example 파일을 복사해 .env 라는 이름으로 만들고 APP_IMAGE 값을 채우세요.
    goto :FAIL
)

where docker >nul 2>&1
if errorlevel 1 (
    echo [오류] docker 명령을 찾을 수 없습니다.
    echo Docker Desktop이 설치되어 있지 않은 것 같습니다.
    echo docker.com 에서 Docker Desktop을 설치한 뒤 PC를 재부팅하고 다시 실행하세요.
    goto :FAIL
)

echo [1/5] Docker Desktop 실행 상태를 확인합니다...
docker info >nul 2>&1
if not errorlevel 1 goto :DOCKER_READY

echo Docker Desktop이 꺼져 있습니다. 실행을 시도합니다...
set "DOCKER_DESKTOP_EXE=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if exist "%DOCKER_DESKTOP_EXE%" (
    start "" "%DOCKER_DESKTOP_EXE%"
) else (
    echo [안내] Docker Desktop 실행 파일을 자동으로 찾지 못했습니다.
    echo 시작 메뉴에서 Docker Desktop을 직접 실행한 뒤 이 창을 다시 실행해 주세요.
)

echo Docker Desktop이 준비될 때까지 최대 2분 기다립니다...
set /a DOCKER_WAIT=0
:WAIT_DOCKER_LOOP
timeout /t 3 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto :DOCKER_READY
set /a DOCKER_WAIT+=3
if !DOCKER_WAIT! GEQ 120 (
    echo [오류] Docker Desktop이 2분 안에 준비되지 않았습니다.
    echo 작업 표시줄의 Docker 고래 아이콘이 움직임을 멈춘 뒤 다시 실행해 주세요.
    goto :FAIL
)
goto :WAIT_DOCKER_LOOP

:DOCKER_READY
echo Docker Desktop이 준비되었습니다.
echo.

echo [2/5] 앱 이미지를 확인합니다. 처음 실행이라면 몇 분 걸릴 수 있습니다...
docker compose --env-file .env -f compose.dist.yaml pull
if errorlevel 1 (
    call :CHECK_LOCAL_IMAGE
    if errorlevel 1 (
        echo [오류] 이미지를 내려받지 못했고, 이 PC에 저장된 이미지도 없습니다.
        echo 인터넷 연결을 확인하거나, offline 폴더의 안내에 따라 오프라인 이미지를 먼저 불러오세요.
        goto :FAIL
    ) else (
        echo [안내] 인터넷에서 최신 이미지를 확인하지 못해 이 PC에 저장된 이미지로 계속 진행합니다.
    )
)
echo.

echo [3/5] 앱을 실행합니다...
docker compose --env-file .env -f compose.dist.yaml up -d
if errorlevel 1 (
    echo [오류] 앱 실행에 실패했습니다. 아래 명령으로 로그를 확인하세요.
    echo   docker compose --env-file .env -f compose.dist.yaml logs
    goto :FAIL
)
echo.

echo [4/5] 앱이 응답할 때까지 최대 60초 기다립니다...
set /a HEALTH_WAIT=0
:WAIT_HEALTH_LOOP
curl.exe -s -o nul -w "%%{http_code}" http://127.0.0.1:8080/health > "%TEMP%\csvdlapp_health.txt" 2>nul
set "HEALTH_CODE="
set /p HEALTH_CODE=<"%TEMP%\csvdlapp_health.txt"
if "!HEALTH_CODE!"=="200" goto :HEALTH_OK
timeout /t 2 /nobreak >nul
set /a HEALTH_WAIT+=2
if !HEALTH_WAIT! GEQ 60 (
    echo [오류] 60초 안에 앱이 응답하지 않았습니다.
    echo 아래 명령으로 로그를 확인하세요.
    echo   docker compose --env-file .env -f compose.dist.yaml logs
    del "%TEMP%\csvdlapp_health.txt" >nul 2>&1
    goto :FAIL
)
goto :WAIT_HEALTH_LOOP

:HEALTH_OK
del "%TEMP%\csvdlapp_health.txt" >nul 2>&1
echo 앱이 정상적으로 실행되었습니다.
echo.

echo [5/5] 브라우저를 엽니다...
start "" "http://localhost:8080"

echo.
echo 이 PC의 사설 LAN IP 주소, 참고용:
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' } | Select-Object -First 1 -ExpandProperty IPAddress" > "%TEMP%\csvdlapp_lan_ip.txt" 2>nul
set "LAN_IP="
set /p LAN_IP=<"%TEMP%\csvdlapp_lan_ip.txt"
del "%TEMP%\csvdlapp_lan_ip.txt" >nul 2>&1
if not defined LAN_IP set "LAN_IP=확인 불가"
echo   !LAN_IP!
echo   참고용 정보입니다. 앱은 보안을 위해 이 PC 안에서만 열려 있어 다른 기기에서는 접속할 수 없습니다.
echo.
echo =====================================================
echo  실행 완료. 브라우저에서 자동으로 열리지 않으면 아래 주소로 접속하세요.
echo    http://localhost:8080
echo  종료하려면 종료.bat 을 실행하세요.
echo =====================================================
pause
exit /b 0

:CHECK_LOCAL_IMAGE
set "CHK_APP_IMAGE="
set "CHK_APP_IMAGE_TAG="
for /f "usebackq eol=# tokens=1,2 delims==" %%K in (".env") do (
    if /i "%%K"=="APP_IMAGE" set "CHK_APP_IMAGE=%%L"
    if /i "%%K"=="APP_IMAGE_TAG" set "CHK_APP_IMAGE_TAG=%%L"
)
if not defined CHK_APP_IMAGE set "CHK_APP_IMAGE=ghcr.io/OWNER/csv-dl-app"
if not defined CHK_APP_IMAGE_TAG set "CHK_APP_IMAGE_TAG=cpu"
docker image inspect "!CHK_APP_IMAGE!:!CHK_APP_IMAGE_TAG!" >nul 2>&1
exit /b %ERRORLEVEL%

:FAIL
echo.
echo 실행이 중단되었습니다.
pause
exit /b 1
