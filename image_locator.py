"""Local screenshot template matching without external services."""

from __future__ import annotations

import ctypes
from pathlib import Path

import numpy as np
from PIL import Image, ImageGrab


USER32 = ctypes.WinDLL("user32", use_last_error=True)
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77

# 模板缓存：(路径, mtime, 大小, 缩放档) → 缩放后数组；(…, FFT 形状) → 翻转 FFT。
# 图像定位每步都重新全屏匹配，模板部分与屏幕内容无关，缓存后每次少一次
# 全尺寸 FFT 与文件解码；FIFO 限量防止换用大量模板时内存无界增长。
_TEMPLATE_ARRAY_CACHE: dict[tuple, np.ndarray] = {}
_TEMPLATE_FFT_CACHE: dict[tuple, np.ndarray] = {}
_TEMPLATE_CACHE_LIMIT = 12


class ImageNotFoundError(RuntimeError):
    pass


def virtual_screen_origin() -> tuple[int, int]:
    return USER32.GetSystemMetrics(SM_XVIRTUALSCREEN), USER32.GetSystemMetrics(SM_YVIRTUALSCREEN)


def locate_template(template_path: Path, threshold: float = 0.86) -> tuple[int, int, float]:
    if not template_path.exists():
        raise ImageNotFoundError(f"图像模板不存在：{template_path.name}")
    screenshot = ImageGrab.grab(all_screens=True).convert("L")
    with Image.open(template_path) as source:
        template_width, template_height = source.size
    if template_width > screenshot.width or template_height > screenshot.height:
        raise ImageNotFoundError("图像模板大于当前桌面范围")

    scale = _matching_scale(template_width, template_height)
    screen_array = _as_array(screenshot, scale)
    scaled_w = max(4, round(template_width * scale))
    scaled_h = max(4, round(template_height * scale))
    fft_shape = (screen_array.shape[0] + scaled_h - 1, screen_array.shape[1] + scaled_w - 1)
    template_array, template_fft = _cached_template(template_path, scale, fft_shape)
    coarse_x, coarse_y, score = _normalized_cross_correlation(screen_array, template_array, template_fft)
    if score < threshold:
        raise ImageNotFoundError(f"未找到目标图像，最高相似度 {score:.0%}，要求 {threshold:.0%}")

    screen_x, screen_y = virtual_screen_origin()
    match_x = round(coarse_x / scale + template_width / 2) + screen_x
    match_y = round(coarse_y / scale + template_height / 2) + screen_y
    return match_x, match_y, score


def _matching_scale(width: int, height: int) -> float:
    largest = max(width, height)
    if largest >= 240:
        return 0.35
    if largest >= 100:
        return 0.5
    return 0.75


def _as_array(image: Image.Image, scale: float) -> np.ndarray:
    if scale != 1:
        size = (max(4, round(image.width * scale)), max(4, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.BILINEAR)
    return np.asarray(image, dtype=np.float32)


def _cache_put(cache: dict, key: tuple, value) -> None:
    if len(cache) >= _TEMPLATE_CACHE_LIMIT:
        cache.pop(next(iter(cache)))
    cache[key] = value


def _cached_template(template_path: Path, scale: float, fft_shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    stat = template_path.stat()
    base_key = (str(template_path), stat.st_mtime_ns, stat.st_size, scale)
    array = _TEMPLATE_ARRAY_CACHE.get(base_key)
    if array is None:
        array = _as_array(Image.open(template_path).convert("L"), scale)
        _cache_put(_TEMPLATE_ARRAY_CACHE, base_key, array)
    fft_key = (*base_key, fft_shape)
    fft = _TEMPLATE_FFT_CACHE.get(fft_key)
    if fft is None:
        fft = np.fft.rfftn(np.flip(array), fft_shape, axes=(0, 1))
        _cache_put(_TEMPLATE_FFT_CACHE, fft_key, fft)
    return array, fft


def _normalized_cross_correlation(image: np.ndarray, template: np.ndarray, template_fft: np.ndarray) -> tuple[int, int, float]:
    image_height, image_width = image.shape
    template_height, template_width = template.shape
    if float(template.var()) < 1:
        raise ImageNotFoundError("图像模板内容过于单一，请选择包含文字或边缘的区域")

    fft_shape = (image_height + template_height - 1, image_width + template_width - 1)
    image_fft = np.fft.rfftn(image, fft_shape, axes=(0, 1))
    correlation = np.fft.irfftn(image_fft * template_fft, fft_shape, axes=(0, 1))
    valid = correlation[template_height - 1:image_height, template_width - 1:image_width]

    integral_sq = _integral_image(image.astype(np.float64) ** 2)
    local_sum_sq = _window_sum(integral_sq, template_height, template_width)
    sample_count = template_height * template_width
    squared_error = np.maximum(local_sum_sq - 2 * valid + float(np.sum(template.astype(np.float64) ** 2)), 0)
    normalized_error = np.sqrt(squared_error / (sample_count * 255.0 * 255.0))
    scores = np.clip(1.0 - normalized_error, 0.0, 1.0)
    flat_index = int(np.argmax(scores))
    y, x = np.unravel_index(flat_index, scores.shape)
    return int(x), int(y), float(scores[y, x])


def _integral_image(values: np.ndarray) -> np.ndarray:
    integral = np.cumsum(np.cumsum(values, axis=0), axis=1)
    return np.pad(integral, ((1, 0), (1, 0)), mode="constant")


def _window_sum(integral: np.ndarray, height: int, width: int) -> np.ndarray:
    return integral[height:, width:] - integral[:-height, width:] - integral[height:, :-width] + integral[:-height, :-width]
