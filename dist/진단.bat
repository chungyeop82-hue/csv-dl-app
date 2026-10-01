@echo off
chcp 949 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title CSV 딥러닝 웹앱 - 진단

echo =====================================================
echo  CSV 딥러닝 웹앱 - 실행 환경 진단
echo  일부 항목은 수 초에서 수십 초가 걸릴 수 있습니다.
echo =====================================================
echo.

set "TMP1=%TEMP%\csvdlapp_diag_1.txt"
set "TMP2=%TEMP%\csvdlapp_diag_2.txt"

echo [1] Windows 가상화
powershell -NoProfile -Command "(Get-CimInstance Win32_ComputerSystem).HypervisorPresent" > "%TMP1%" 2>nul
set "HYPERV="
set /p HYPERV=<"%TMP1%"
if /i "!HYPERV!"=="True" (
    echo   결과: 통과. 가상화가 활성화되어 있습니다.
) else (
    echo   결과: 확인 필요.
    echo   확인 방법: 작업 관리자, 성능, CPU 탭에서 가상화 사용 여부를 보세요.
    echo   꺼져 있다면 BIOS에서 Intel VT-x 또는 AMD-V 가상화를 켜야 합니다.
)
echo.

echo [2] WSL 2
where wsl >nul 2>&1
if errorlevel 1 (
    echo   결과: 실패. wsl 명령을 찾을 수 없습니다. Windows 기능에서 WSL을 설치하세요.
) else (
    echo   아래는 wsl --status 의 원본 출력입니다. 기본 버전 항목이 2 이면 정상입니다.
    wsl --status
)
echo.

echo [3] RAM
powershell -NoProfile -Command "[math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)" > "%TMP1%" 2>nul
set "RAM_GB="
set /p RAM_GB=<"%TMP1%"
if not defined RAM_GB (
    echo   결과: 확인 실패.
) else (
    echo   측정값: 약 !RAM_GB! GB
    if !RAM_GB! GEQ 8 (
        echo   결과: 통과. 8GB 이상입니다.
    ) else (
        echo   결과: 기준 미달. 8GB 이상을 권장합니다.
    )
)
echo.

echo [4] 디스크 여유 공간
set "DIST_DRIVE=%~d0"
powershell -NoProfile -Command "[math]::Floor((Get-PSDrive -Name '!DIST_DRIVE:~0,1!').Free/1GB)" > "%TMP1%" 2>nul
set "DISK_GB="
set /p DISK_GB=<"%TMP1%"
if not defined DISK_GB (
    echo   결과: 확인 실패. 드라이브: !DIST_DRIVE!
) else (
    echo   드라이브: !DIST_DRIVE! 측정값: 약 !DISK_GB! GB
    if !DISK_GB! GEQ 15 (
        echo   결과: 통과. 여유 15GB 이상입니다.
    ) else (
        echo   결과: 기준 미달. 여유 15GB 이상, GPU 이미지는 20GB 이상을 권장합니다.
    )
)
echo.

echo [5] 8080 포트
netstat -ano | findstr :8080 > "%TMP1%" 2>nul
for %%Z in ("%TMP1%") do set "PORT_SIZE=%%~zZ"
if "!PORT_SIZE!"=="0" (
    echo   결과: 사용 가능. 8080 포트를 쓰는 프로그램이 없습니다.
) else (
    echo   결과: 확인 필요. 아래 프로그램이 이미 8080 포트를 쓰고 있습니다.
    echo   이미 이 앱이 실행 중이라면 정상입니다. 그렇지 않다면 다른 프로그램과 충돌할 수 있습니다.
    type "%TMP1%"
)
echo.

echo [6] Docker
where docker >nul 2>&1
if errorlevel 1 (
    echo   결과: 실패. docker 명령을 찾을 수 없습니다. Docker Desktop을 설치하세요.
) else (
    for /f "usebackq delims=" %%V in (`docker --version`) do echo   버전: %%V
    docker info >nul 2>&1
    if errorlevel 1 (
        echo   결과: 확인 필요. Docker Desktop 엔진이 꺼져 있는 것 같습니다. 앱을 실행하세요.
    ) else (
        echo   결과: 통과. Docker 엔진이 실행 중입니다.
    )
)
echo.

echo [7] Docker Compose
where docker >nul 2>&1
if errorlevel 1 (
    echo   결과: 건너뜀. Docker가 없어 확인할 수 없습니다.
) else (
    docker compose version > "%TMP1%" 2>nul
    if errorlevel 1 (
        echo   결과: 실패. docker compose 를 사용할 수 없습니다. Docker Desktop을 최신 버전으로 올리세요.
    ) else (
        echo   결과: 통과.
        type "%TMP1%"
    )
)
echo.

echo [8] nvidia-smi
where nvidia-smi >nul 2>&1
if errorlevel 1 (
    set "NVSMI_OK=0"
    echo   결과: 해당 없음. NVIDIA GPU가 없거나 드라이버가 설치되어 있지 않습니다. CPU 모드로 진행하세요.
) else (
    nvidia-smi >nul 2>&1
    if errorlevel 1 (
        set "NVSMI_OK=0"
        echo   결과: 실패. nvidia-smi 실행에 문제가 있습니다. 드라이버를 확인하세요.
    ) else (
        set "NVSMI_OK=1"
        echo   결과: 통과. NVIDIA GPU를 찾았습니다.
    )
)
echo.

echo [9] Docker GPU 사용 가능 여부
if not "!NVSMI_OK!"=="1" (
    echo   결과: 건너뜀. GPU가 없어 확인하지 않습니다.
) else (
    where docker >nul 2>&1
    if errorlevel 1 (
        echo   결과: 건너뜀. Docker가 없어 확인할 수 없습니다.
    ) else (
        echo   확인 중입니다. 처음 확인할 때는 인터넷이 필요할 수 있습니다...
        docker run --rm --gpus all ubuntu nvidia-smi >nul 2>&1
        if errorlevel 1 (
            echo   결과: 실패. Docker 컨테이너에서 GPU를 인식하지 못했습니다.
            echo   원인 예시: 드라이버가 WSL2를 지원하지 않는 구버전이거나 인터넷 연결이 없는 경우입니다.
        ) else (
            echo   결과: 통과. Docker에서 GPU를 인식합니다.
        )
    )
)
echo.

echo [10] PyTorch CUDA 사용 가능 여부
set "APP_RUNNING=0"
if exist ".env" (
    docker compose --env-file .env -f compose.dist.yaml ps -q app > "%TMP2%" 2>nul
    for %%Z in ("%TMP2%") do if not "%%~zZ"=="0" set "APP_RUNNING=1"
)
if "!APP_RUNNING!"=="0" (
    echo   결과: 건너뜀. 앱이 실행 중이 아닙니다. 먼저 시작.bat 또는 시작_GPU.bat 을 실행하세요.
) else (
    docker compose --env-file .env -f compose.dist.yaml exec -T app python -c "import torch; print(torch.__version__, torch.cuda.is_available())" > "%TMP1%" 2>nul
    if errorlevel 1 (
        echo   결과: 확인 실패. 앱 컨테이너에 접속하지 못했습니다.
    ) else (
        echo   결과 확인:
        type "%TMP1%"
        echo   True 이면 GPU를 사용 중, False 이면 CPU를 사용 중입니다. CPU 모드에서는 False 가 정상입니다.
    )
)
echo.

echo [11] /health 상태
if "!APP_RUNNING!"=="0" (
    echo   결과: 건너뜀. 앱이 실행 중이 아닙니다.
) else (
    curl.exe -s -o "%TMP1%" -w "%%{http_code}" http://127.0.0.1:8080/health > "%TMP2%" 2>nul
    set "HCODE="
    set /p HCODE=<"%TMP2%"
    if "!HCODE!"=="200" (
        echo   결과: 통과. 상태 코드 200 정상 응답.
    ) else (
        echo   결과: 실패. 상태 코드: !HCODE!
    )
)
echo.

echo [12] LAN IPv4 주소 (스마트폰 접속용)
set "DIAG_LAN_IP="
if exist "scripts\get_lan_ip.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\get_lan_ip.ps1" > "%TMP1%" 2>nul
    set /p DIAG_LAN_IP=<"%TMP1%"
)
if not defined DIAG_LAN_IP (
    echo   결과: 확인 불가. Windows 설정 - 네트워크 및 인터넷에서 Wi-Fi의 IPv4 주소를 직접 확인하세요.
) else (
    echo   결과: !DIAG_LAN_IP!
)
echo.

echo [13] 8080 포트 LISTEN 상태 (0.0.0.0 바인딩 여부)
netstat -ano | findstr "LISTENING" | findstr "0.0.0.0:8080" > "%TMP1%" 2>nul
for %%Z in ("%TMP1%") do set "LISTEN_SIZE=%%~zZ"
if "!LISTEN_SIZE!"=="0" (
    echo   결과: 확인 필요. 0.0.0.0:8080 으로 LISTEN 중인 프로세스를 찾지 못했습니다.
    echo   원인 예시: 앱이 꺼져 있거나, 아직 이전 버전(127.0.0.1 전용)으로 실행 중일 수 있습니다.
    echo   시작.bat 또는 시작_GPU.bat 을 다시 실행해 보세요.
) else (
    echo   결과: 정상. 0.0.0.0:8080 으로 LISTEN 중이라 LAN의 다른 기기에서도 접속할 수 있습니다.
)
echo.

echo [14] Windows Firewall 상태 (TCP 8080 인바운드)
powershell -NoProfile -Command "$r = Get-NetFirewallRule -DisplayName 'CSV 딥러닝 웹앱 LAN 8080' -ErrorAction SilentlyContinue; if ($r) { if ($r.Enabled -eq 1 -or $r.Enabled -eq 'True') { 'ALLOW' } else { 'DISABLED' } } else { 'NONE' }" > "%TMP1%" 2>nul
set "FW_DIAG="
set /p FW_DIAG=<"%TMP1%"
if "!FW_DIAG!"=="ALLOW" (
    echo   결과: 정상. CSV 딥러닝 웹앱 LAN 8080 규칙으로 TCP 8080 인바운드가 허용되어 있습니다.
) else if "!FW_DIAG!"=="DISABLED" (
    echo   결과: 확인 필요. 규칙은 있으나 비활성화 상태입니다. 방화벽_허용.bat 을 다시 실행하거나 Windows 방화벽 설정에서 활성화하세요.
) else (
    echo   결과: 확인 필요. 아직 허용 규칙이 없습니다. 스마트폰 접속이 안 되면 방화벽_허용.bat 을 실행하세요.
    echo   ^(이 진단에서는 사용자 확인 없이 방화벽 설정을 바꾸지 않습니다.^)
)
echo.

echo [15] 스마트폰 접속 주소
if not defined DIAG_LAN_IP (
    echo   결과: LAN IPv4를 확인할 수 없어 주소를 만들 수 없습니다. 위 [12] 항목을 참고하세요.
) else (
    echo   PC:       http://localhost:8080
    echo   스마트폰:  http://!DIAG_LAN_IP!:8080
    echo   QR 코드:  http://localhost:8080/qr?text=http://!DIAG_LAN_IP!:8080
)
echo.

del "%TMP1%" >nul 2>&1
del "%TMP2%" >nul 2>&1

echo =====================================================
echo  진단이 끝났습니다. 위 결과에서 실패, 확인 필요 항목을
echo  안내 문구를 따라 하나씩 해결한 뒤 시작.bat 을 다시 실행하세요.
echo =====================================================
pause
exit /b 0
