"""QR 코드 인코더(app/qrcode_gen.py) 자체 검증 (STEP 10).

이 모듈은 segno/qrcode 같은 외부 QR 라이브러리에 의존하지 않고 표준 라이브러리만으로
작성되었다. 개발 중에는 OpenCV(cv2.QRCodeDetector)로 실제 왕복 디코딩까지 확인했지만,
cv2 는 이 프로젝트의 의존성이 아니므로(웹 프로세스가 torch 도 가져오지 않는 것과 같은
이유) 여기서는 의존성 없이 할 수 있는 구조적/대수적 자기 검증만 수행한다.
"""

from __future__ import annotations

import random

import pytest

from app.qrcode_gen import (
    GF_EXP,
    VERSION_TABLE,
    encode,
    gf_mul,
    matrix_size,
    max_text_length,
    pick_version,
    rs_encode,
    to_svg,
)


def _is_valid_rs_codeword(codewords: list[int], ec_len: int) -> bool:
    """RS 코드워드 블록은 생성다항식의 각 근(2^i)에서 값이 0이어야 한다."""
    for i in range(ec_len):
        root = GF_EXP[i]
        acc = 0
        for coef in codewords:
            acc = gf_mul(acc, root) ^ coef
        if acc != 0:
            return False
    return True


def test_rs_encode_produces_algebraically_valid_codewords():
    random.seed(0)
    for ec_len in (10, 16, 26):
        for _ in range(15):
            n = random.randint(1, 44)
            data = [random.randint(0, 255) for _ in range(n)]
            ec = rs_encode(data, ec_len)
            assert len(ec) == ec_len
            assert _is_valid_rs_codeword(data + ec, ec_len)


def test_version_table_is_monotonic_and_boundaries_encode():
    versions = sorted(VERSION_TABLE)
    assert versions == [1, 2, 3]
    limits = [max_text_length(v) for v in versions]
    assert limits == sorted(limits)  # 버전이 올라가면 용량도 늘어난다

    for version in versions:
        limit = max_text_length(version)
        at_limit = "a" * limit
        assert pick_version(at_limit) == version

        mat = encode(at_limit, version=version)
        assert len(mat) == matrix_size(version)
        assert all(cell in (0, 1) for row in mat for cell in row)

        if version == versions[-1]:
            with pytest.raises(ValueError):
                encode("a" * (limit + 1))
        else:
            over = "a" * (limit + 1)
            assert pick_version(over) == version + 1


def test_encode_rejects_non_ascii_text():
    with pytest.raises(ValueError):
        encode("한글주소불가")


def test_to_svg_is_csp_safe_and_well_formed():
    mat = encode("http://192.168.0.10:8080")
    svg = to_svg(mat, scale=8, border=4)
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    # style-src 'self' 를 지키려고 인라인 style/script 를 전혀 쓰지 않는다.
    assert "style=" not in svg
    assert "<script" not in svg
    assert svg.count("<rect") >= 1


def test_realistic_lan_urls_all_fit_within_version_3():
    for text in (
        "http://192.168.0.1:8080",
        "http://192.168.100.123:8080",
        "http://10.0.0.5:8080",
        "http://10.255.255.255:8080",
    ):
        version = pick_version(text)
        assert version is not None, text
        mat = encode(text, version=version)
        assert len(mat) == matrix_size(version)
