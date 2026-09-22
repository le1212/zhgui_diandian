"""Local screenshot template matching without external services."""

from __future__ import annotations

import ctypes
from pathlib import Path

import numpy as np
from PIL import Image, ImageGrab


USER32 = ctypes.WinDLL("user32", use_last_error=True)
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77


class ImageNotFoundError(RuntimeError):
    pass


def virtual_screen_origin() -> tuple[int, int]:
    return USER32.GetSystemMetrics(SM_XVIRTUALSCREEN), USER32.GetSystemMetrics(SM_YVIRTUALSCREEN)


def locate_template(template_path: Path, threshold: float = 0.86) -> tuple[int, int, float]:
    if not template_path.exists():
        raise ImageNotFoundError(f"图像模板不存在：{template_path.name}")
    screenshot = ImageGrab.grab(all_screens=True).convert("L")
    template = Image.open(template_path).convert("L")
    if template.width > screenshot.width or template.height > screenshot.height:
        raise ImageNotFoundError("图像模板大于当前桌面范围")

    scale = _matching_scale(template.width, template.height)
    screen_array = _as_array(screenshot, scale)
    template_array = _as_array(template, scale)
    coarse_x, coarse_y, score = _normalized_cross_correlation(screen_array, template_array)
    if score < threshold:
        raise ImageNotFoundError(f"未找到目标图像，最高相似度 {score:.0%}，要求 {threshold:.0%}")

    screen_x, screen_y = virtual_screen_origin()
    match_x = round(coarse_x / scale + template.width / 2) + screen_x
    match_y = round(coarse_y / scale + template.height / 2) + screen_y
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


def _normalized_cross_correlation(image: np.ndarray, template: np.ndarray) -> tuple[int, int, float]:
    image_height, image_width = image.shape
    template_height, template_width = template.shape
    template_variance = float(template.var())
    if template_variance < 1:
        raise ImageNotFoundError("图像模板内容过于单一，请选择包含文字或边缘的区域")

    fft_shape = (image_height + template_height - 1, image_width + template_width - 1)
    axes = (0, 1)
    image_fft = np.fft.rfftn(image, fft_shape, axes=axes)
    template_fft = np.fft.rfftn(np.flip(template), fft_shape, axes=axes)
    correlation = np.fft.irfftn(image_fft * template_fft, fft_shape, axes=axes)
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
