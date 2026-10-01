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
echo 같은 Wi-Fi(LAN)에 연결된 스마트폰 접속 주소를 확인합니다...
set "LAN_IP="
if exist "scripts\get_lan_ip.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\get_lan_ip.ps1" > "%TEMP%\csvdlapp_lan_ip.txt" 2>nul
    set /p LAN_IP=<"%TEMP%\csvdlapp_lan_ip.txt"
    del "%TEMP%\csvdlapp_lan_ip.txt" >nul 2>&1
)

echo.
echo =====================================================
echo  실행 완료.
echo   PC:      http://localhost:8080
if defined LAN_IP (
    echo   스마트폰: http://!LAN_IP!:8080   ^(같은 Wi-Fi에서만 접속 가능^)
    echo.
    echo  스마트폰 카메라로 찍을 QR 코드 페이지를 추가로 엽니다...
    start "" "http://localhost:8080/qr?text=http://!LAN_IP!:8080"
    echo  스마트폰에서 접속이 안 되면:
    echo   1^) PC와 스마트폰이 같은 Wi-Fi에 연결되어 있는지 확인하세요.
    echo   2^) 방화벽_허용.bat 을 실행해 8080 포트를 허용했는지 확인하세요.
    echo   3^) 진단.bat 으로 자세한 상태를 확인하세요.
) else (
    echo   스마트폰: 확인 불가합니다.
    echo   Windows 설정 - 네트워크 및 인터넷에서 Wi-Fi의 IPv4 주소를 직접 확인한 뒤
    echo   스마트폰 브라우저에 http://^<해당 IP^>:8080 을 입력해 접속해 보세요.
)
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
