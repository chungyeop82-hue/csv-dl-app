@echo off
chcp 949 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title CSV 딥러닝 웹앱 - 초기화

echo =====================================================
echo  경고 - 데이터 초기화
echo =====================================================
echo  이 작업은 업로드한 CSV, 학습된 모델, 학습 이력 데이터베이스를
echo  전부 삭제하고 처음 상태로 되돌립니다.
echo  exports 폴더에 이미 내보낸 결과물, 제출 ZIP 등은 삭제하지 않습니다.
echo =====================================================
echo.
set /p CONFIRM=정말 초기화하시겠습니까. 계속하려면 Y 를 입력하고 Enter 를 누르세요:

if /i not "%CONFIRM%"=="Y" (
    echo.
    echo 취소되었습니다. 아무 것도 삭제하지 않았습니다.
    pause
    exit /b 0
)

where docker >nul 2>&1
if errorlevel 1 (
    echo [오류] docker 명령을 찾을 수 없습니다. Docker Desktop이 설치되어 있는지 확인하세요.
    pause
    exit /b 1
)

echo.
echo 컨테이너를 종료하고 데이터를 삭제합니다...
docker compose --env-file .env -f compose.dist.yaml down -v
if errorlevel 1 (
    echo [오류] 초기화 중 문제가 발생했습니다. Docker Desktop이 실행 중인지 확인하세요.
    pause
    exit /b 1
)

echo.
echo 초기화가 완료되었습니다. 시작.bat 또는 시작_GPU.bat 을 실행하면 새로 시작할 수 있습니다.
pause
exit /b 0
