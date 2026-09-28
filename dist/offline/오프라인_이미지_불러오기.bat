@echo off
chcp 949 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title CSV 딥러닝 웹앱 - 오프라인 이미지 불러오기

echo =====================================================
echo  오프라인 배포용 이미지 불러오기
echo  인터넷이 안 되는 PC에서 실행하세요.
echo =====================================================
echo.

where docker >nul 2>&1
if errorlevel 1 (
    echo [오류] docker 명령을 찾을 수 없습니다. Docker Desktop이 설치되어 있는지 확인하세요.
    pause
    exit /b 1
)

if not exist "offline_images\csv-dl-app-cpu.tar" (
    echo [오류] offline_images\csv-dl-app-cpu.tar 파일을 찾을 수 없습니다.
    echo 이미지를 저장한 PC에서 만든 offline_images 폴더를 이 폴더 옆에 그대로 복사했는지 확인하세요.
    pause
    exit /b 1
)

echo CPU 이미지를 불러옵니다...
docker load -i "offline_images\csv-dl-app-cpu.tar"
if errorlevel 1 (
    echo [오류] CPU 이미지를 불러오지 못했습니다. Docker Desktop이 실행 중인지 확인하세요.
    pause
    exit /b 1
)

if exist "offline_images\csv-dl-app-gpu.tar" (
    echo GPU 이미지를 불러옵니다...
    docker load -i "offline_images\csv-dl-app-gpu.tar"
)

echo.
echo =====================================================
echo  이미지를 불러왔습니다.
echo  이제 상위 폴더의 시작.bat 을 실행하세요.
echo  GPU 이미지도 불러왔다면 시작_GPU.bat 을 사용해도 됩니다.
echo  인터넷이 없어도 실행됩니다.
echo =====================================================
pause
exit /b 0
