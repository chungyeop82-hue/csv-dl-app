"""이미지 빌드 중 실행: torch/torchvision/torchaudio 버전 일치와 CPU/GPU 빌드 종류를 검증한다.

사용법: python check_torch.py cpu|gpu
기대 버전은 빌드 ARG(환경 변수)에서 읽는다.
"""

import os
import sys

import torch
import torchaudio
import torchvision

kind = sys.argv[1] if len(sys.argv) > 1 else ""
assert kind in ("cpu", "gpu"), "인자는 cpu 또는 gpu 여야 한다"


def release(version: str) -> str:
    # 2.9.1+cpu, 2.9.1+cu128 처럼 붙는 빌드 태그를 제거한다.
    return version.split("+")[0]


expected = {
    "torch": os.environ["TORCH_VERSION"],
    "torchvision": os.environ["TORCHVISION_VERSION"],
    "torchaudio": os.environ["TORCHAUDIO_VERSION"],
}
actual = {
    "torch": torch.__version__,
    "torchvision": torchvision.__version__,
    "torchaudio": torchaudio.__version__,
}

for name, want in expected.items():
    assert release(actual[name]) == want, f"{name}: 기대 {want}, 실제 {actual[name]}"

if kind == "cpu":
    assert torch.version.cuda is None, f"CPU 이미지에 CUDA 빌드가 설치됨: {torch.version.cuda}"
else:
    assert torch.version.cuda is not None, "GPU 이미지에 CUDA 빌드가 설치되지 않음"

print(f"[{kind}] {actual} | torch.version.cuda={torch.version.cuda}")
