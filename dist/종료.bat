@echo off
chcp 949 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title CSV 딥러닝 웹앱 - 종료

echo =====================================================
echo  CSV 딥러닝 웹앱 종료
echo =====================================================
echo.

where docker >nul 2>&1
if errorlevel 1 (
    echo [오류] docker 명령을 찾을 수 없습니다. Docker Desktop이 설치되어 있는지 확인하세요.
    pause
    exit /b 1
)

echo 실행 중인 컨테이너를 종료합니다...
docker compose --env-file .env -f compose.dist.yaml down
if errorlevel 1 (
    echo [오류] 종료 중 문제가 발생했습니다. Docker Desktop이 실행 중인지 확인하세요.
    pause
    exit /b 1
)

echo.
echo 종료되었습니다. 저장된 데이터와 exports 폴더의 결과물은 그대로 남아 있습니다.
echo 다시 시작하려면 시작.bat 또는 시작_GPU.bat 을 실행하세요.
pause
exit /b 0
