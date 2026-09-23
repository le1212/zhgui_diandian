"""主题层守护：两套调色板键集合必须一致，防止深色模式缺色启动报错。"""

from theme import THEME_LIGHT, THEME_DARK


def test_light_and_dark_have_identical_token_sets():
    assert set(THEME_LIGHT) == set(THEME_DARK)
    for token, value in THEME_LIGHT.items():
        assert isinstance(value, str) and value.startswith("#")
        assert isinstance(THEME_DARK[token], str) and THEME_DARK[token].startswith("#")
