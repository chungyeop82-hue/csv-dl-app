"""장치 선택. APP_DEVICE(auto|cpu|cuda)와 torch.cuda.is_available() 로 고르고, CPU 면 스레드를 2개로 제한한다.

torch 는 함수 안에서만 가져온다(웹 프로세스가 이 모듈을 가져와도 torch 가 로드되지 않게 함).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

CPU_THREADS = 2
VALID_REQUESTS = ("auto", "cpu", "cuda")


@dataclass(frozen=True)
class DeviceChoice:
    device: str  # "cpu" | "cuda"
    requested: str  # 요청 값(auto|cpu|cuda)
    reason: str  # 화면·로그에 남길 한국어 사유
    cuda_name: str | None = None
    threads: int | None = None  # CPU 모드에서 적용한 스레드 수


def requested_device(environ=None) -> str:
    """환경 변수 APP_DEVICE 를 읽는다. 없거나 알 수 없는 값이면 auto."""
    env = os.environ if environ is None else environ
    value = str(env.get("APP_DEVICE", "auto")).strip().lower()
    return value if value in VALID_REQUESTS else "auto"


def select_device(requested: str | None = None, torch_module=None) -> DeviceChoice:
    """장치를 고르고, CPU 를 고르면 torch.set_num_threads(2) 를 적용한다.

    - cpu: 항상 CPU
    - auto: torch.cuda.is_available() 이면 GPU, 아니면 CPU
    - cuda: 가능하면 GPU, 불가능하면 CPU 로 내려가고 사유를 남김
    """
    torch = torch_module
    if torch is None:
        import torch  # noqa: PLC0415 - 지연 import

    if requested is None:
        requested = requested_device()
    requested = requested.strip().lower()
    if requested not in VALID_REQUESTS:
        requested = "auto"

    cuda_ok = requested != "cpu" and bool(torch.cuda.is_available())
    if cuda_ok:
        try:
            name = str(torch.cuda.get_device_name(0))
        except Exception:  # noqa: BLE001 - 이름을 못 읽어도 GPU 사용에는 지장 없음
            name = None
        return DeviceChoice("cuda", requested, "GPU를 사용합니다.", cuda_name=name)

    torch.set_num_threads(CPU_THREADS)
    if requested == "cpu":
        reason = "CPU 모드로 실행합니다."
    elif requested == "cuda":
        reason = "GPU를 쓸 수 없어 CPU로 실행합니다."
    else:
        reason = "GPU가 없어 CPU로 실행합니다."
    return DeviceChoice("cpu", requested, reason, threads=CPU_THREADS)
