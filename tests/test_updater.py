"""自动更新模块单元测试：版本比较、清单校验、24 小时节流与下载完整性。"""

from __future__ import annotations

import email.message
import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path

import updater
from task_repository import TaskRepository
from version import APP_VERSION


class FakeResponse:
    """urlopen 桩：JSON 响应与分块下载共用，read(size) 按预切块依次返回。"""

    def __init__(self, data: bytes, chunk_size: int | None = None) -> None:
        self.headers = email.message.Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"
        step = chunk_size if chunk_size else len(data)
        self._chunks = [data[index:index + step] for index in range(0, len(data), step)]

    def read(self, size: int = -1) -> bytes:
        if size == -1:
            payload = b"".join(self._chunks)
            self._chunks = []
            return payload
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _manifest(**overrides) -> dict:
    payload = {
        "version": "9.9.9",
        "notes": "重大更新",
        "pageUrl": "https://dd.zhigui.icu/",
        "installerUrl": "https://dd.zhigui.icu/Diandian-Setup-9.9.9.exe",
        "sha256": "a" * 64,
        "size": 123,
        "minVersion": "",
        "publishedAt": "2026-09-23",
    }
    payload.update(overrides)
    return payload


class VersionTests(unittest.TestCase):
    def test_parse_version_tolerates_prefix_and_suffix(self) -> None:
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updater.parse_version(" 1.2.3-beta "), (1, 2, 3))
        self.assertEqual(updater.parse_version("1.2"), (1, 2))
        self.assertIsNone(updater.parse_version(""))
        self.assertIsNone(updater.parse_version("latest"))
        self.assertIsNone(updater.parse_version("1.x"))

    def test_is_newer_version_compares_numerically(self) -> None:
        self.assertTrue(updater.is_newer_version("1.10.0", "1.9.0"))
        self.assertTrue(updater.is_newer_version("1.2.1", "1.2"))
        self.assertFalse(updater.is_newer_version("1.2.0", "1.2.0"))
        self.assertFalse(updater.is_newer_version("1.1.9", "1.2"))
        self.assertFalse(updater.is_newer_version("bad", APP_VERSION))

    def test_is_update_required_only_when_current_below_minimum(self) -> None:
        info = updater.UpdateInfo.from_payload(_manifest(minVersion="2.0.0"))
        self.assertTrue(updater.is_update_required(info))
        info = updater.UpdateInfo.from_payload(_manifest(minVersion="0.9.0"))
        self.assertFalse(updater.is_update_required(info))


class ManifestTests(unittest.TestCase):
    def test_from_payload_keeps_verifiable_installer(self) -> None:
        info = updater.UpdateInfo.from_payload(_manifest())
        self.assertEqual(info.version, "9.9.9")
        self.assertEqual(info.installer_url, "https://dd.zhigui.icu/Diandian-Setup-9.9.9.exe")
        self.assertEqual(info.sha256, "a" * 64)
        self.assertEqual(info.size, 123)

    def test_from_payload_degrades_without_checksum(self) -> None:
        info = updater.UpdateInfo.from_payload(_manifest(sha256=""))
        self.assertIsNone(info.installer_url)
        self.assertIsNone(info.sha256)

    def test_from_payload_requires_https(self) -> None:
        with self.assertRaises(updater.ManifestError):
            updater.UpdateInfo.from_payload(_manifest(installerUrl="http://evil.example/setup.exe"))
        with self.assertRaises(updater.ManifestError):
            updater.UpdateInfo.from_payload(_manifest(pageUrl="ftp://dd.zhigui.icu/"))

    def test_from_payload_rejects_unparsable_version(self) -> None:
        with self.assertRaises(updater.ManifestError):
            updater.UpdateInfo.from_payload(_manifest(version=""))

    def test_from_payload_drops_invalid_min_version(self) -> None:
        info = updater.UpdateInfo.from_payload(_manifest(minVersion="not-a-version"))
        self.assertIsNone(info.min_version)

    def test_fetch_update_returns_parsed_manifest(self) -> None:
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["url"] = request.full_url
            seen["agent"] = request.headers.get("User-agent")
            return FakeResponse(json.dumps(_manifest()).encode("utf-8"))

        info = updater.fetch_update(url="https://dd.zhigui.icu/updates/latest.json", urlopen=fake_urlopen)
        self.assertEqual(info.version, "9.9.9")
        self.assertEqual(seen["url"], "https://dd.zhigui.icu/updates/latest.json")
        self.assertTrue(seen["agent"].startswith("Diandian/"))

    def test_fetch_update_wraps_network_and_format_errors(self) -> None:
        def http_error(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 503, "unavailable", None, None)

        with self.assertRaises(updater.UpdateError):
            updater.fetch_update(url="https://dd.zhigui.icu/updates/latest.json", urlopen=http_error)
        with self.assertRaises(updater.UpdateError):
            updater.fetch_update(url="https://dd.zhigui.icu/updates/latest.json",
                                 urlopen=lambda request, timeout=None: FakeResponse(b"not json"))


class CheckThrottleTests(unittest.TestCase):
    def test_should_check_respects_interval(self) -> None:
        now = 1_000_000.0
        self.assertTrue(updater.should_check(0.0, now))
        self.assertFalse(updater.should_check(now - updater.CHECK_INTERVAL_SECONDS + 10, now))
        self.assertTrue(updater.should_check(now - updater.CHECK_INTERVAL_SECONDS, now))


class UpdateStateTests(unittest.TestCase):
    def test_repository_roundtrip_and_corruption_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = TaskRepository(Path(directory))
            self.assertEqual(repository.load_update_state(), {})
            repository.save_update_state({"last_check": 123.5, "skipped_version": "1.0.0"})
            state = repository.load_update_state()
            self.assertEqual(state["last_check"], 123.5)
            self.assertEqual(state["skipped_version"], "1.0.0")
            repository.update_state_path.write_text("{broken", encoding="utf-8")
            self.assertEqual(repository.load_update_state(), {})


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)
        self.body = b"Diandian-installer-payload" * 100

    def _info(self, **overrides) -> updater.UpdateInfo:
        payload = _manifest(
            sha256=hashlib.sha256(self.body).hexdigest(),
            size=len(self.body),
        )
        payload.update(overrides)
        return updater.UpdateInfo.from_payload(payload)

    def test_download_success_reports_progress_and_verifies_hash(self) -> None:
        progress: list[tuple[int, int]] = []
        path = updater.download_installer(
            self._info(),
            progress=lambda received, total: progress.append((received, total)),
            urlopen=lambda request, timeout=None: FakeResponse(self.body, chunk_size=256),
            destination_dir=self.directory,
        )
        self.assertEqual(path.name, "Diandian-Setup-9.9.9.exe")
        self.assertEqual(path.read_bytes(), self.body)
        self.assertEqual(progress[-1], (len(self.body), len(self.body)))
        self.assertTrue(progress[0][0] > 0)

    def test_download_rejects_unverifiable_installer(self) -> None:
        info = self._info(sha256="")
        with self.assertRaises(updater.UpdateError):
            updater.download_installer(info, destination_dir=self.directory)

    def test_download_aborts_on_hash_mismatch(self) -> None:
        info = self._info(sha256="b" * 64)
        with self.assertRaises(updater.UpdateError):
            updater.download_installer(info, urlopen=lambda request, timeout=None: FakeResponse(self.body),
                                       destination_dir=self.directory)
        self.assertEqual(list(self.directory.glob("*.exe")), [])

    def test_download_aborts_on_size_mismatch(self) -> None:
        info = self._info(size=len(self.body) + 1)
        with self.assertRaises(updater.UpdateError):
            updater.download_installer(info, urlopen=lambda request, timeout=None: FakeResponse(self.body),
                                       destination_dir=self.directory)
        self.assertEqual(list(self.directory.glob("*.exe")), [])

    def test_download_cancelled_leaves_no_file(self) -> None:
        cancel = threading.Event()
        cancel.set()

        def fake_urlopen(request, timeout=None):
            self.fail("取消后不应发起请求")

        with self.assertRaises(updater.UpdateCancelled):
            updater.download_installer(self._info(), cancel=cancel, urlopen=fake_urlopen,
                                       destination_dir=self.directory)

    def test_download_clears_stale_installers(self) -> None:
        stale = self.directory / "Diandian-Setup-0.9.0.exe"
        stale.write_bytes(b"old")
        updater.download_installer(self._info(), urlopen=lambda request, timeout=None: FakeResponse(self.body),
                                   destination_dir=self.directory)
        self.assertFalse(stale.exists())


class SafeUrlTests(unittest.TestCase):
    def test_https_allowed_and_plain_http_blocked(self) -> None:
        self.assertTrue(updater._is_safe_url("https://dd.zhigui.icu/Diandian-Setup-1.1.0.exe"))
        self.assertTrue(updater._is_safe_url("http://localhost:8931/updates/latest.json"))
        self.assertTrue(updater._is_safe_url("http://127.0.0.1:8931/updates/latest.json"))
        self.assertFalse(updater._is_safe_url("http://dd.zhigui.icu/Diandian.exe"))
        self.assertFalse(updater._is_safe_url("ftp://dd.zhigui.icu/x"))
        self.assertFalse(updater._is_safe_url("not-a-url"))

    def test_fragment_cannot_spoof_localhost_http(self) -> None:
        self.assertFalse(updater._is_safe_url("http://evil.com#@localhost/setup.exe"))
        self.assertFalse(updater._is_safe_url("http://user@evil.com:80#@127.0.0.1/x"))


class VerifyInstallerTests(unittest.TestCase):
    def test_verify_installer_recomputes_hash_before_execute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Diandian-Setup-9.9.9.exe"
            path.write_bytes(b"payload-for-verify")
            good = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertTrue(updater.verify_installer(path, good))
            self.assertTrue(updater.verify_installer(path, good.upper()))
            self.assertFalse(updater.verify_installer(path, "0" * 64))


class InstalledBuildTests(unittest.TestCase):
    def test_source_checkout_is_not_installed_build(self) -> None:
        self.assertFalse(updater.is_installed_build())


if __name__ == "__main__":
    unittest.main()
