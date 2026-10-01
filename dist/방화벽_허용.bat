@echo off
chcp 949 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title CSV 딥러닝 웹앱 - 방화벽 허용 (TCP 8080)

echo =====================================================
echo  Windows 방화벽 - TCP 8080 인바운드 허용
echo  스마트폰 등 같은 Wi-Fi(LAN)의 다른 기기에서 이 PC의 8080 포트로
echo  접속하려면 Windows 방화벽에서 TCP 8080 인바운드를 허용해야 할 수 있습니다.
echo  이 스크립트는 사용자 확인(Y) 없이는 방화벽 설정을 바꾸지 않습니다.
echo =====================================================
echo.

net session >nul 2>&1
if not errorlevel 1 goto :ADMIN_OK

echo 관리자 권한이 필요합니다. 관리자 권한으로 다시 실행합니다...
echo 잠시 뒤 Windows 사용자 계정 컨트롤(UAC) 창이 뜨면 "예"를 눌러 주세요.
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b 0

:ADMIN_OK
echo 관리자 권한을 확인했습니다.
echo.

echo 기존 방화벽 규칙을 확인합니다...
powershell -NoProfile -Command "if (Get-NetFirewallRule -DisplayName 'CSV 딥러닝 웹앱 LAN 8080' -ErrorAction SilentlyContinue) { 'EXISTS' } else { 'NONE' }" > "%TEMP%\csvdlapp_fw_check.txt" 2>nul
set "FW_STATE="
set /p FW_STATE=<"%TEMP%\csvdlapp_fw_check.txt"
del "%TEMP%\csvdlapp_fw_check.txt" >nul 2>&1

if "%FW_STATE%"=="EXISTS" (
    echo 이미 CSV 딥러닝 웹앱 LAN 8080 규칙이 등록되어 있습니다. 추가로 할 일이 없습니다.
    echo.
    goto :DONE
)

echo 아직 TCP 8080 인바운드를 허용하는 규칙이 없습니다.
echo 지금 추가하면 같은 Wi-Fi(LAN)의 다른 기기가 이 PC의 8080 포트로 접속할 수 있게 됩니다.
echo 인터넷(외부망)에는 영향이 없으며, 이 PC가 속한 로컬 네트워크(LAN)에만 적용됩니다.
echo.
set /p CONFIRM=방화벽 규칙을 추가하시겠습니까. 계속하려면 Y 를 입력하고 Enter 를 누르세요:

if /i not "%CONFIRM%"=="Y" (
    echo.
    echo 취소되었습니다. 방화벽 설정을 바꾸지 않았습니다.
    goto :DONE
)

echo.
echo 방화벽 규칙을 추가합니다...
powershell -NoProfile -Command "New-NetFirewallRule -DisplayName 'CSV 딥러닝 웹앱 LAN 8080' -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Any | Out-Null" 2>"%TEMP%\csvdlapp_fw_err.txt"
if errorlevel 1 (
    echo [오류] 방화벽 규칙 추가에 실패했습니다. 아래 내용을 확인하세요.
    type "%TEMP%\csvdlapp_fw_err.txt" 2>nul
    del "%TEMP%\csvdlapp_fw_err.txt" >nul 2>&1
    goto :DONE
)
del "%TEMP%\csvdlapp_fw_err.txt" >nul 2>&1
echo 완료되었습니다. CSV 딥러닝 웹앱 LAN 8080 규칙이 추가되었습니다.

:DONE
echo.
echo =====================================================
echo  진단.bat 을 실행하면 방화벽 상태를 다시 확인할 수 있습니다.
echo =====================================================
pause
exit /b 0
