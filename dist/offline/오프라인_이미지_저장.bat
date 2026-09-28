@echo off
chcp 949 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0\.."
title CSV 딥러닝 웹앱 - 오프라인 배포용 이미지 저장

echo =====================================================
echo  오프라인 배포용 이미지 저장
echo  인터넷이 되는 PC에서 실행하세요.
echo =====================================================
echo.

if not exist ".env" (
    echo [오류] .env 파일이 없습니다.
    echo .env.example 파일을 복사해 .env 라는 이름으로 만들고 APP_IMAGE 값을 채운 뒤 다시 실행하세요.
    pause
    exit /b 1
)

where docker >nul 2>&1
if errorlevel 1 (
    echo [오류] docker 명령을 찾을 수 없습니다. Docker Desktop이 설치되어 있는지 확인하세요.
    pause
    exit /b 1
)

set "APP_IMAGE="
set "APP_IMAGE_TAG="
set "APP_IMAGE_TAG_GPU="
for /f "usebackq eol=# tokens=1,2 delims==" %%K in (".env") do (
    if /i "%%K"=="APP_IMAGE" set "APP_IMAGE=%%L"
    if /i "%%K"=="APP_IMAGE_TAG" set "APP_IMAGE_TAG=%%L"
    if /i "%%K"=="APP_IMAGE_TAG_GPU" set "APP_IMAGE_TAG_GPU=%%L"
)
if not defined APP_IMAGE (
    echo [오류] .env 에 APP_IMAGE 값이 없습니다.
    pause
    exit /b 1
)
if not defined APP_IMAGE_TAG set "APP_IMAGE_TAG=cpu"
if not defined APP_IMAGE_TAG_GPU set "APP_IMAGE_TAG_GPU=gpu"

set "OUT_DIR=%~dp0offline_images"
if not exist "!OUT_DIR!" mkdir "!OUT_DIR!"

echo CPU 이미지를 받아옵니다: !APP_IMAGE!:!APP_IMAGE_TAG!
docker pull "!APP_IMAGE!:!APP_IMAGE_TAG!"
if errorlevel 1 (
    echo [오류] CPU 이미지를 받아오지 못했습니다. 인터넷 연결과 APP_IMAGE 값을 확인하세요.
    pause
    exit /b 1
)
echo CPU 이미지를 파일로 저장합니다...
docker save -o "!OUT_DIR!\csv-dl-app-cpu.tar" "!APP_IMAGE!:!APP_IMAGE_TAG!"
echo 저장 위치: !OUT_DIR!\csv-dl-app-cpu.tar

echo.
set /p WITH_GPU=GPU 이미지도 저장할까요. NVIDIA GPU용이며 용량이 매우 큽니다. Y 또는 N 을 입력하세요:
if /i "!WITH_GPU!"=="Y" (
    echo GPU 이미지를 받아옵니다: !APP_IMAGE!:!APP_IMAGE_TAG_GPU!
    docker pull "!APP_IMAGE!:!APP_IMAGE_TAG_GPU!"
    if errorlevel 1 (
        echo [오류] GPU 이미지를 받아오지 못했습니다. CPU 이미지만 저장된 상태로 계속합니다.
    ) else (
        echo GPU 이미지를 파일로 저장합니다. 수 GB 크기라 시간이 걸릴 수 있습니다.
        docker save -o "!OUT_DIR!\csv-dl-app-gpu.tar" "!APP_IMAGE!:!APP_IMAGE_TAG_GPU!"
        echo 저장 위치: !OUT_DIR!\csv-dl-app-gpu.tar
    )
)

echo.
echo =====================================================
echo  완료되었습니다.
echo  offline_images 폴더와 dist 폴더 전체를 USB 등으로
echo  옮긴 뒤, 오프라인 PC의 offline 폴더에서
echo  오프라인_이미지_불러오기.bat 을 실행하세요.
echo =====================================================
pause
exit /b 0
