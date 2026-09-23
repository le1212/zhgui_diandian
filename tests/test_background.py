from __future__ import annotations

import ctypes
import unittest
from ctypes import wintypes

import autostart
from winapi import acquire_mutex


class SingleInstanceTests(unittest.TestCase):
    def test_second_acquire_in_same_process_returns_none(self) -> None:
        handle = acquire_mutex("Diandian.TestMutex")
        try:
            self.assertIsNotNone(handle)
            self.assertIsNone(acquire_mutex("Diandian.TestMutex"))
        finally:
            if handle:
                KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
                KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
                KERNEL32.CloseHandle(handle)


class AutostartTests(unittest.TestCase):
    def test_command_quotes_paths(self) -> None:
        command = autostart.autostart_command()
        self.assertTrue(command.startswith('"'))
        self.assertIn('"', command[1:])
        if getattr(__import__("sys"), "frozen", False):
            self.assertEqual(command.count('"'), 2)
        else:
            self.assertGreaterEqual(command.count('"'), 4)

    def test_registry_roundtrip(self) -> None:
        if autostart.is_enabled():
            self.skipTest("用户已配置开机自启，避免改动真实注册表状态")
        try:
            self.assertTrue(autostart.set_enabled(True))
            self.assertTrue(autostart.is_enabled())
        finally:
            # 断言失败或中断时也必须清理，避免开发机残留自启状态
            autostart.set_enabled(False)
        self.assertFalse(autostart.is_enabled())
        # 关闭一个不存在的值也应返回成功（幂等）
        self.assertTrue(autostart.set_enabled(False))


if __name__ == "__main__":
    unittest.main()
