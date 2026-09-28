"""이미지 빌드 중 실행: 나눔고딕 인식과 matplotlib 설정을 검증한다."""

import io

import matplotlib
from matplotlib import font_manager
from matplotlib import pyplot as plt

# 나눔고딕을 찾지 못하면 fallback 없이 즉시 실패시킨다.
font_path = font_manager.findfont("NanumGothic", fallback_to_default=False)
font = font_manager.get_font(font_path)
missing = [ch for ch in "한글가나다" if font.get_char_index(ord(ch)) == 0]
assert not missing, f"NanumGothic 에 없는 글자: {missing}"

assert matplotlib.get_backend().lower() == "agg", matplotlib.get_backend()
assert matplotlib.rcParams["axes.unicode_minus"] is False
assert matplotlib.rcParams["font.sans-serif"][0] == "NanumGothic"

# 실제로 한글과 마이너스가 든 그림을 그려 본다.
fig = plt.figure(figsize=(2, 1))
fig.text(0.5, 0.5, "한글 -1", ha="center")
buffer = io.BytesIO()
fig.savefig(buffer, format="png")
plt.close(fig)
assert buffer.tell() > 0

print(f"matplotlib {matplotlib.__version__} | font={font_path}")
