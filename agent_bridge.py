"""Dispatch to independent Claude and Codex status adapters without changing their CLI."""
import argparse
import importlib
import sys


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=["claude", "codex"])
    provider = parser.parse_args(arguments[:1]).provider
    module = {"claude": "claude_bridge", "codex": "codex_adapter"}[provider]
    previous = sys.argv
    try:
        sys.argv = [module + ".py", *arguments[1:]]
        return importlib.import_module(module).main()
    finally:
        sys.argv = previous


if __name__ == "__main__":
    raise SystemExit(main())
