# get_lan_ip.ps1 (STEP 10)
# PC의 실제 LAN(Wi-Fi/Ethernet) IPv4 주소를 한 줄만 표준출력으로 내보낸다.
# 호출 쪽(시작.bat 등)은 이 출력을 LAN_IP 변수로 받아 스마트폰 접속 주소를 만든다.
#
# 제외 대상: 127.0.0.1, 169.254.*(APIPA 자동 할당), Docker/WSL/Hyper-V/VPN 등 가상 어댑터.
# 우선순위: 연결됨(Up) 상태의 실제 어댑터 중 192.168.x.x > 10.x.x.x > 172.16~31.x.x > 그 외.
# 아무 것도 찾지 못하면 아무 줄도 출력하지 않는다 (호출 쪽에서 "확인 불가"로 처리).

$ErrorActionPreference = 'SilentlyContinue'

# Docker Desktop(WSL2 백엔드) 가상 스위치, WSL, Hyper-V, 각종 VPN/터널 어댑터는 LAN 접속과 무관하므로 제외한다.
$excludePattern = 'vEthernet|Docker|WSL|Loopback|Virtual|Npcap|Tailscale|ZeroTier|VPN|Hyper-V|VMware|VirtualBox|Bluetooth'

$candidates = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.IPAddress -ne '127.0.0.1' -and
        $_.IPAddress -notlike '169.254.*' -and
        $_.InterfaceAlias -notmatch $excludePattern
    }

if (-not $candidates) {
    exit 0
}

$scored = foreach ($c in $candidates) {
    $adapter = Get-NetAdapter -InterfaceIndex $c.InterfaceIndex -ErrorAction SilentlyContinue
    $isUp = $true
    $descOk = $true
    if ($adapter) {
        $isUp = ($adapter.Status -eq 'Up')
        $descOk = ($adapter.InterfaceDescription -notmatch $excludePattern)
    }
    if ($isUp -and $descOk) {
        $rank = 3
        if ($c.IPAddress -like '192.168.*') { $rank = 0 }
        elseif ($c.IPAddress -like '10.*') { $rank = 1 }
        elseif ($c.IPAddress -match '^172\.(1[6-9]|2[0-9]|3[0-1])\.') { $rank = 2 }
        [PSCustomObject]@{ IP = $c.IPAddress; Rank = $rank }
    }
}

$best = $scored | Sort-Object Rank | Select-Object -First 1
if ($best) {
    Write-Output $best.IP
}
