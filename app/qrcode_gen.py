"""표준 라이브러리만 사용하는 최소 QR 코드(Model 2) 인코더 - STEP 10 스마트폰 LAN 접속 QR 전용.

범위를 의도적으로 좁혀서 짧은 ASCII LAN 주소(예: http://192.168.0.10:8080)만 인코딩한다.
  - 바이트(byte) 모드만 지원한다.
  - 오류 정정 레벨 M만 지원한다.
  - 버전 1~3만 지원한다 (레벨 M 기준 최대 데이터 42바이트, 단일 RS 블록 - 인터리빙 없음).
  - 마스크 패턴은 0으로 고정한다 (스펙상 8개 중 어떤 것을 써도 되며, "최적" 마스크가
    필요한 것이 아니라 올바르게 "선언된" 마스크만 있으면 된다).

이 샌드박스 환경에서는 pypi.org 등 패키지 저장소에 접근할 수 없어 segno/qrcode 같은
QR 생성 라이브러리를 설치할 수 없었다. 그래서 외부 디코더(OpenCV)로 왕복 디코딩까지
검증한 뒤 표준 라이브러리만으로 새로 작성했다 (OpenCV는 검증에만 썼고, 이 모듈이나
앱 자체는 OpenCV에 의존하지 않는다).
"""
from __future__ import annotations

# ---- GF(256) 연산 테이블 (원시다항식 0x11D, 생성원 2) ----
GF_EXP = [0] * 512
GF_LOG = [0] * 256


def _init_gf() -> None:
    x = 1
    for i in range(255):
        GF_EXP[i] = x
        GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        GF_EXP[i] = GF_EXP[i - 255]


_init_gf()


def gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]


def rs_generator_poly(ec_len: int) -> list[int]:
    """(x - 2^0)(x - 2^1)...(x - 2^(ec_len-1)) in GF(256). 최고차항 계수 1(monic), 최고차항부터."""
    poly = [1]
    for i in range(ec_len):
        root = GF_EXP[i]
        new_poly = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            new_poly[j] ^= coef
            new_poly[j + 1] ^= gf_mul(coef, root)
        poly = new_poly
    return poly


def rs_encode(data: list[int], ec_len: int) -> list[int]:
    gen = rs_generator_poly(ec_len)
    msg = data + [0] * ec_len
    for i in range(len(data)):
        coef = msg[i]
        if coef != 0:
            for j, g in enumerate(gen):
                msg[i + j] ^= gf_mul(g, coef)
    return msg[len(data):]


# ---------------------------------------------------------------------------
# 매트릭스(모듈 격자) 구성
# ---------------------------------------------------------------------------

ALIGNMENT_CENTER = {2: 18, 3: 22, 4: 26, 5: 30, 6: 34}
FORMAT_BCH_GEN = 0b10100110111  # 차수 10, 포맷 정보에 쓰는 (15,5) BCH
FORMAT_MASK = 0b101010000010010


def matrix_size(version: int) -> int:
    return 17 + 4 * version


def _is_function_module(version: int, size: int, row: int, col: int) -> bool:
    # 파인더 패턴 + 분리자 (3개 모서리의 8x8 영역)
    if row < 8 and col < 8:
        return True
    if row < 8 and col >= size - 8:
        return True
    if row >= size - 8 and col < 8:
        return True
    # 타이밍 패턴
    if row == 6 or col == 6:
        return True
    # 정렬 패턴 (버전 2 이상): (c, c) 중심의 5x5
    if version >= 2:
        c = ALIGNMENT_CENTER[version]
        if abs(row - c) <= 2 and abs(col - c) <= 2:
            return True
    # 포맷 정보 영역 (좌상단 파인더 주변, 타이밍과 중복되지 않는 칸)
    if row == 8 and col < 9:
        return True
    if col == 8 and row < 9:
        return True
    if row == 8 and col >= size - 8:
        return True
    if col == 8 and row >= size - 7:
        return True
    return False


def build_function_matrix(version: int):
    size = matrix_size(version)
    mat = [[None] * size for _ in range(size)]

    def set_block(r0, c0, pattern):
        for dr, row in enumerate(pattern):
            for dc, v in enumerate(row):
                mat[r0 + dr][c0 + dc] = v

    finder = [
        [1, 1, 1, 1, 1, 1, 1],
        [1, 0, 0, 0, 0, 0, 1],
        [1, 0, 1, 1, 1, 0, 1],
        [1, 0, 1, 1, 1, 0, 1],
        [1, 0, 1, 1, 1, 0, 1],
        [1, 0, 0, 0, 0, 0, 1],
        [1, 1, 1, 1, 1, 1, 1],
    ]
    set_block(0, 0, finder)
    set_block(0, size - 7, finder)
    set_block(size - 7, 0, finder)

    align = [
        [1, 1, 1, 1, 1],
        [1, 0, 0, 0, 1],
        [1, 0, 1, 0, 1],
        [1, 0, 0, 0, 1],
        [1, 1, 1, 1, 1],
    ]
    if version >= 2:
        c = ALIGNMENT_CENTER[version]
        set_block(c - 2, c - 2, align)

    for i in range(size):
        if mat[6][i] is None:
            mat[6][i] = 1 if i % 2 == 0 else 0
        if mat[i][6] is None:
            mat[i][6] = 1 if i % 2 == 0 else 0

    # 고정 다크 모듈
    mat[size - 8][8] = 1

    # 아직 채우지 않은 포맷 정보 예약 칸은 일단 0으로 둔다 (나중에 실제 값으로 채움)
    for row in range(size):
        for col in range(size):
            if mat[row][col] is None and _is_function_module(version, size, row, col):
                mat[row][col] = 0

    return mat


def data_module_positions(version: int):
    """표준 지그재그(열 2개씩, 아래→위→아래 반복) 배치 순서, 우하단에서 좌상단으로."""
    size = matrix_size(version)
    fn = [[_is_function_module(version, size, r, c) for c in range(size)] for r in range(size)]
    positions = []
    col = size - 1
    going_up = True
    while col > 0:
        cols = (col, col - 1)
        rows = range(size - 1, -1, -1) if going_up else range(size)
        for row in rows:
            for c in cols:
                if c == 6:
                    continue
                if fn[row][c]:
                    continue
                positions.append((row, c))
        going_up = not going_up
        col -= 2
    return positions


# ---------------------------------------------------------------------------
# 데이터 코드워드 구성 (바이트 모드, 버전 1~9는 문자수 필드 8비트)
# ---------------------------------------------------------------------------

def build_data_codewords(text: bytes, data_capacity: int) -> list[int]:
    bits: list[int] = []

    def push(value: int, nbits: int) -> None:
        for i in range(nbits - 1, -1, -1):
            bits.append((value >> i) & 1)

    push(0b0100, 4)  # 바이트 모드
    push(len(text), 8)
    for b in text:
        push(b, 8)

    cap_bits = data_capacity * 8
    if len(bits) > cap_bits:
        raise ValueError("text too long for this QR version/level")

    push(0, min(4, cap_bits - len(bits)))
    while len(bits) % 8 != 0:
        bits.append(0)

    pad_bytes = [0xEC, 0x11]
    i = 0
    while len(bits) < cap_bits:
        push(pad_bytes[i % 2], 8)
        i += 1

    codewords = []
    for i in range(0, len(bits), 8):
        byte = 0
        for b in bits[i:i + 8]:
            byte = (byte << 1) | b
        codewords.append(byte)
    return codewords


def bch_format_bits(ec_level_bits: int, mask_pattern: int) -> list[int]:
    data = (ec_level_bits << 3) | mask_pattern  # 5비트
    value = data << 10
    gen = FORMAT_BCH_GEN
    for shift in range(4, -1, -1):
        if value & (1 << (10 + shift)):
            value ^= gen << shift
    full = (data << 10) | value
    full ^= FORMAT_MASK
    return [(full >> i) & 1 for i in range(14, -1, -1)]


def apply_mask0_and_place(mat, version: int, codewords: list[int]):
    size = matrix_size(version)
    bits = []
    for cw in codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)
    positions = data_module_positions(version)
    for idx, (row, col) in enumerate(positions):
        bit = bits[idx] if idx < len(bits) else 0
        mat[row][col] = bit

    for row, col in positions:
        if (row + col) % 2 == 0:
            mat[row][col] ^= 1

    # 포맷 정보: 오류 정정 레벨 M = 00, 마스크 패턴 0
    fmt_bits = bch_format_bits(0b00, 0)
    for i in range(6):
        mat[8][i] = fmt_bits[i]
    mat[8][7] = fmt_bits[6]
    mat[8][8] = fmt_bits[7]
    mat[7][8] = fmt_bits[8]
    for i in range(6):
        mat[5 - i][8] = fmt_bits[9 + i]
    for i in range(7):
        mat[size - 1 - i][8] = fmt_bits[i]
    for i in range(8):
        mat[8][size - 8 + i] = fmt_bits[7 + i]

    # data_module_positions() 가 모든 비-함수 모듈을 빠짐없이 돌려준다고 가정하지 않고,
    # 방어적으로 아직 값이 없는 칸은 0(밝은 모듈)으로 채운다. to_svg() 등 이 매트릭스를
    # 읽는 쪽은 모두 참/거짓(0도 None도 "밝다") 기준이라 동작은 바뀌지 않지만,
    # encode() 가 돌려주는 매트릭스는 항상 0/1 로만 채워진 상태여야 한다.
    for row in range(size):
        for col in range(size):
            if mat[row][col] is None:
                mat[row][col] = 0
    return mat


# 오류 정정 레벨 M, 단일 RS 블록 기준 (data_codewords, ec_codewords).
# 버전 1/2는 OpenCV 왕복 디코드로 직접 확인했고, 버전 3은 경계값 탐색(너무 길면
# ValueError, 그 경계에서 디코드 성공 여부)으로 교차 확인했다.
VERSION_TABLE: dict[int, tuple[int, int]] = {
    1: (16, 10),
    2: (28, 16),
    3: (44, 26),
}


def max_text_length(version: int) -> int:
    data_cw, _ = VERSION_TABLE[version]
    cap_bits = data_cw * 8
    # 모드(4) + 문자수(8) + 종료자(최대4), 바이트 단위로 내림.
    return (cap_bits - 12) // 8


def pick_version(text: str) -> int | None:
    for v in (1, 2, 3):
        if len(text.encode("ascii", errors="strict")) <= max_text_length(v):
            return v
    return None


def encode(text: str, version: int | None = None):
    """ASCII 문자열을 QR 모듈 매트릭스(0/1 2차원 리스트)로 인코딩한다."""
    if version is None:
        version = pick_version(text)
        if version is None:
            raise ValueError("text too long for this QR version/level")
    data_cw_count, ec_cw_count = VERSION_TABLE[version]
    data_cw = build_data_codewords(text.encode("ascii"), data_cw_count)
    ec_cw = rs_encode(data_cw, ec_cw_count)
    mat = build_function_matrix(version)
    apply_mask0_and_place(mat, version, data_cw + ec_cw)
    return mat


def to_svg(mat, scale: int = 6, border: int = 4) -> str:
    """모듈 매트릭스를 CSP(style-src 'self')에 맞는, 인라인 스타일 없는 SVG 문자열로 그린다."""
    size = len(mat)
    px = (size + 2 * border) * scale
    rects = []
    for r in range(size):
        row_start = None
        for c in range(size + 1):
            dark = c < size and mat[r][c]
            if dark and row_start is None:
                row_start = c
            elif not dark and row_start is not None:
                x = (row_start + border) * scale
                y = (r + border) * scale
                w = (c - row_start) * scale
                rects.append(f'<rect x="{x}" y="{y}" width="{w}" height="{scale}"/>')
                row_start = None
    body = "".join(rects)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {px} {px}" '
        f'width="{px}" height="{px}" shape-rendering="crispEdges">'
        f'<rect width="{px}" height="{px}" fill="#ffffff"/>'
        f'<g fill="#000000">{body}</g></svg>'
    )
