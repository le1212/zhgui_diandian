"""应用自更新：获取版本清单、比较版本、下载并校验安装包、启动静默安装。

更新链路：启动后异步请求官网清单 → 比较版本号 → 命中新版本时在主窗口展示
横幅 → 用户确认后下载安装包（边下边算 SHA-256，校验不过绝不执行）→ 退出
本程序并拉起静默安装器，由 Inno Setup 完成覆盖升级后自动重启新版。

本模块不依赖 Tk：网络、校验与版本逻辑均可脱离界面做单元测试。设计约束：
- 清单与安装包只允许 HTTPS（本机 HTTP 仅用于联调）；
- 清单缺少安装包地址或 SHA-256 时自动降级为「前往下载页」，不执行静默安装；
- 环境变量 DIANDIAN_UPDATE_URL 可覆盖清单地址，便于内测与故障排查。
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from version import APP_VERSION

DEFAULT_MANIFEST_URL = "https://dd.zhigui.icu/updates/latest.json"
USER_AGENT = f"Diandian/{APP_VERSION} (Windows)"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
MAX_INSTALLER_BYTES = 256 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
# 静默安装参数：不出向导、自动关闭占用中的点点，安装完成后由 [Run] 拉起新版
INSTALLER_SWITCHES = ("/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS")
_DOWNLOAD_CHUNK = 64 * 1024
_HEX_DIGITS = set("0123456789abcdef")


class UpdateError(Exception):
    """更新失败原因，文案可直接展示给用户。"""


class ManifestError(UpdateError):
    """更新清单缺失、损坏或地址不安全。"""


class UpdateCancelled(Exception):
    """用户在下载过程中取消。"""


@dataclass(frozen=True)
class UpdateInfo:
    """解析并校验后的更新清单。

    installer_url 为 None 表示本次清单无法安全地静默安装（缺地址或缺哈希），
    只保留跳转下载页的引导。
    """

    version: str
    notes: str
    page_url: str
    installer_url: str | None
    sha256: str | None
    size: int | None
    min_version: str | None

    @classmethod
    def from_payload(cls, payload: object) -> "UpdateInfo":
        if not isinstance(payload, dict):
            raise ManifestError("更新清单格式不正确")
        version = _clean_text(payload, "version")
        if parse_version(version) is None:
            raise ManifestError("更新清单的版本号无法解析")
        page_url = _clean_text(payload, "pageUrl")
        if not _is_safe_url(page_url):
            raise ManifestError("更新清单的下载页地址不安全")
        installer_url = _clean_text(payload, "installerUrl") or None
        if installer_url and not _is_safe_url(installer_url):
            raise ManifestError("更新清单的安装包地址不安全")
        sha256 = _clean_text(payload, "sha256").lower() or None
        if sha256 and (len(sha256) != 64 or set(sha256) - _HEX_DIGITS):
            raise ManifestError("更新清单的安装包校验值格式不正确")
        size = payload.get("size")
        if not isinstance(size, int) or not 0 < size <= MAX_INSTALLER_BYTES:
            size = None
        min_version = _clean_text(payload, "minVersion") or None
        if min_version is not None and parse_version(min_version) is None:
            min_version = None
        notes = _clean_text(payload, "notes")
        if installer_url and not sha256:
            installer_url = None
        return cls(
            version=version,
            notes=notes,
            page_url=page_url,
            installer_url=installer_url,
            sha256=sha256,
            size=size,
            min_version=min_version,
        )


def manifest_url() -> str:
    """更新清单地址；环境变量覆盖仅用于内测，不参与发布流程。"""
    return os.environ.get("DIANDIAN_UPDATE_URL") or DEFAULT_MANIFEST_URL


def parse_version(text: str) -> tuple[int, ...] | None:
    """把 "v1.2.3" 之类的版本串解析成整数元组；无法解析时返回 None。"""
    core = text.strip().lower().removeprefix("v").split("-")[0].split("+")[0]
    if not core:
        return None
    try:
        return tuple(int(part) for part in core.split("."))
    except ValueError:
        return None


def is_newer_version(candidate: str, current: str) -> bool:
    """candidate 是否严格新于 current；任一版本号无法解析时按无更新处理。"""
    latest = parse_version(candidate)
    present = parse_version(current)
    if latest is None or present is None:
        return False
    width = max(len(latest), len(present))
    return _padded(latest, width) > _padded(present, width)


def is_update_required(info: UpdateInfo) -> bool:
    """当前版本低于清单要求的最低版本（用于重大缺陷后的强制升级）。"""
    return bool(info.min_version) and is_newer_version(info.min_version, APP_VERSION)


def should_check(last_check: float, now: float, interval: float = CHECK_INTERVAL_SECONDS) -> bool:
    return now - last_check >= interval


def fetch_update(url: str | None = None, timeout: float = 8.0,
                 urlopen: Callable | None = None) -> UpdateInfo | None:
    """拉取并校验更新清单；网络或格式问题抛 UpdateError，是否算更新由调用方比较版本。"""
    opener = urlopen or _OPENER.open
    request = urllib.request.Request(url or manifest_url(), headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read()
    except urllib.error.HTTPError as error:
        raise UpdateError(f"更新服务器返回 {error.code}") from error
    except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
        raise UpdateError("无法连接更新服务器，请检查网络") from error
    try:
        try:
            payload = json.loads(body.decode(charset))
        except LookupError:
            # 服务器声明了本机不认识的字符集时退回 UTF-8
            payload = json.loads(body.decode("utf-8", "replace"))
    except ValueError as error:
        raise UpdateError("更新服务器返回了无法解析的内容") from error
    return UpdateInfo.from_payload(payload)


def download_installer(info: UpdateInfo, progress: Callable[[int, int | None], None] | None = None,
                       cancel: threading.Event | None = None, urlopen: Callable | None = None,
                       destination_dir: Path | None = None) -> Path:
    """下载安装包到临时目录并校验 SHA-256；取消或校验失败时不留残余文件。

    progress(received_bytes, total_bytes_or_None) 由下载线程同步调用，界面侧
    自行投递回主线程；cancel 置位后尽快以 UpdateCancelled 退出。
    """
    if not info.installer_url or not info.sha256:
        raise UpdateError("此版本未提供可校验的安装包，请前往下载页手动更新")
    if cancel is not None and cancel.is_set():
        raise UpdateCancelled()
    folder = Path(destination_dir) if destination_dir else default_download_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise UpdateError(f"无法创建下载目录：{error}") from error
    target = folder / f"Diandian-Setup-{info.version}.exe"
    _clear_stale_installers(folder, keep=target.name)
    opener = urlopen or _OPENER.open
    request = urllib.request.Request(info.installer_url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    received = 0
    try:
        with opener(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, target.open("wb") as stream:
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateCancelled()
                chunk = response.read(_DOWNLOAD_CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_INSTALLER_BYTES:
                    raise UpdateError("安装包体积异常，已中止下载")
                digest.update(chunk)
                stream.write(chunk)
                if progress is not None:
                    progress(received, info.size)
    except (UpdateCancelled, UpdateError):
        _discard(target)
        raise
    except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
        _discard(target)
        raise UpdateError(f"下载失败：{error}") from error
    if received == 0:
        _discard(target)
        raise UpdateError("下载内容为空，请稍后重试")
    if digest.hexdigest() != info.sha256:
        _discard(target)
        raise UpdateError("安装包校验失败，已放弃安装")
    if info.size is not None and received != info.size:
        _discard(target)
        raise UpdateError("安装包下载不完整，请稍后重试")
    return target


def is_installed_build() -> bool:
    """当前是否为安装版：绿色版与源码运行没有卸载程序，只能引导到下载页。"""
    if not getattr(sys, "frozen", False):
        return False
    return (Path(sys.executable).parent / "unins000.exe").exists()


def launch_installer(installer_path: Path) -> None:
    """拉起静默安装器；本程序随后自行退出，安装完成由安装器的 [Run] 重启新版。

    剥离 PyInstaller 的 _MEI/_PYI 握手变量：安装器会把环境原样传给 [Run]
    拉起的新版点点，继承这些变量会让新进程跳过解包而崩溃。
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith(("_MEI", "_PYI"))}
    subprocess.Popen(
        [str(installer_path), *INSTALLER_SWITCHES],
        cwd=str(installer_path.parent),
        close_fds=True,
        env=env,
    )


def default_download_dir() -> Path:
    return Path(tempfile.gettempdir()) / "DiandianUpdate"


def _clean_text(payload: dict, key: str) -> str:
    return str(payload.get(key) or "").strip()


def _padded(values: tuple[int, ...], length: int) -> tuple[int, ...]:
    return values + (0,) * (length - len(values))


def _is_safe_url(url: str) -> bool:
    """仅接受 HTTPS；http 只对 localhost 放行，供本地联调清单使用。

    必须用 urlsplit 取主机名：手工切分会把 fragment 误当 authority，
    让 http://evil.com#@localhost 这类地址绕过校验。
    """
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https":
        return bool(host)
    return parts.scheme == "http" and host in ("localhost", "127.0.0.1")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向目标必须仍然安全，防止 HTTPS 下载被 302 降级到任意外部地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_safe_url(newurl):
            raise UpdateError("更新服务器重定向到了不安全的地址，已中止")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirectHandler())


def verify_installer(installer_path: Path, expected_sha256: str) -> bool:
    """对落地安装包重算 SHA-256：下载校验与实际执行之间存在被替换的窗口期。"""
    digest = hashlib.sha256()
    with installer_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_DOWNLOAD_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected_sha256.lower()


def discard_installer(info: UpdateInfo) -> None:
    """删除已落地的安装包（用户取消安装或执行前校验失败时调用）。"""
    _discard(default_download_dir() / f"Diandian-Setup-{info.version}.exe")


def _clear_stale_installers(folder: Path, keep: str) -> None:
    for path in folder.glob("Diandian-Setup-*.exe"):
        if path.name != keep:
            _discard(path)


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
