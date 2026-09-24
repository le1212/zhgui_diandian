"""主题层守护：两套调色板键集合必须一致，防止深色模式缺色启动报错；
中性色必须与墨色同色相（Lovable 式单色派生纪律），防止杂灰混入。"""

from theme import THEME_LIGHT, THEME_DARK

# 语义色白名单：品牌动作色 / 危险色 / 信息色 / 页面专属 hero 色调不受墨色色相约束
_SEMANTIC = {
    "green", "green_hover", "green_text", "green_deep", "green_bright",
    "red", "danger_fg", "danger_bg", "danger_hover", "danger_soft_bg",
    "running_bg", "danger_deep", "running_peak",
    "info_blue", "overlay_accent", "overlay_green", "command_danger",
    "task_active_fg",
    "hero_single", "hero_multi", "hero_record",
}


def test_light_and_dark_have_identical_token_sets():
    assert set(THEME_LIGHT) == set(THEME_DARK)
    for token, value in THEME_LIGHT.items():
        assert isinstance(value, str) and value.startswith("#")
        assert isinstance(THEME_DARK[token], str) and THEME_DARK[token].startswith("#")


def _hsl(value: str) -> tuple[int, float, float]:
    r, g, b = (int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hi, lo = max(r, g, b), min(r, g, b)
    delta = hi - lo
    light = (hi + lo) / 2
    if delta == 0:
        return 0, 0.0, light
    sat = delta / (1 - abs(2 * light - 1))
    if hi is r:
        hue = 60 * (((g - b) / delta) % 6)
    elif hi is g:
        hue = 60 * ((b - r) / delta + 2)
    else:
        hue = 60 * ((r - g) / delta + 4)
    return round(hue), sat, light


def test_neutral_tokens_stay_on_ink_hue():
    """所有中性色与墨色的色相偏差 ≤10°（近白/近无彩的 token 色相无意义，跳过）。"""
    for palette in (THEME_LIGHT, THEME_DARK):
        ink_hue, _, _ = _hsl(palette["ink"])
        for token, value in palette.items():
            if token in _SEMANTIC:
                continue
            hue, sat, light = _hsl(value)
            if sat < 0.08 or light > 0.96:
                continue
            drift = min(abs(hue - ink_hue), 360 - abs(hue - ink_hue))
            assert drift <= 10, f"{token} {value} 色相 {hue:.0f}° 偏离墨色 {ink_hue:.0f}° 达 {drift:.0f}°，疑似杂灰"
