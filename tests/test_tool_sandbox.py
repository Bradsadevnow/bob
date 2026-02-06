import unittest

from bob.tools.sandbox import ToolSandbox


class TestToolSandbox(unittest.TestCase):
    def setUp(self):
        print(f"\n[TEST] {self.__class__.__name__}.{self._testMethodName}")

    def test_disabled_sandbox_blocks(self):
        print("[STEP] Disabled sandbox blocks file access")
        sb = ToolSandbox.disabled()
        with self.assertRaises(PermissionError):
            sb.check_path(".")

    def test_enabled_sandbox_allows_within_roots(self):
        print("[STEP] Enabled sandbox allows only allowlisted roots")
        sb = ToolSandbox.enabled_with_roots(["/tmp"])
        sb.check_path("/tmp")
        with self.assertRaises(PermissionError):
            sb.check_path("/etc/passwd")


if __name__ == "__main__":
    unittest.main()

