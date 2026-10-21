import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agent_bridge


class DispatchTests(unittest.TestCase):
    def test_provider_arguments_and_exit_status_are_preserved(self):
        for provider, module in (("claude", "claude_bridge"), ("codex", "codex_adapter")):
            with self.subTest(provider=provider):
                previous = sys.argv
                seen = []
                def run():
                    seen.append(sys.argv[:])
                    return 2
                with patch("agent_bridge.importlib.import_module", return_value=SimpleNamespace(main=run)) as load:
                    self.assertEqual(agent_bridge.main([provider, "status", "--target", "exact-id"]), 2)
                load.assert_called_once_with(module)
                self.assertEqual(seen, [[module + ".py", "status", "--target", "exact-id"]])
                self.assertIs(sys.argv, previous)

    def test_adapter_help_and_exception_restore_argv(self):
        previous = sys.argv
        def help_adapter():
            self.assertEqual(sys.argv, ["claude_bridge.py", "--help"])
            raise SystemExit(0)
        with patch("agent_bridge.importlib.import_module", return_value=SimpleNamespace(main=help_adapter)):
            with self.assertRaises(SystemExit) as result:
                agent_bridge.main(["claude", "--help"])
        self.assertEqual(result.exception.code, 0)
        self.assertIs(sys.argv, previous)


if __name__ == "__main__":
    unittest.main()
