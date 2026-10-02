"""Fail CI before building/testing when the venv differs from the matrix."""

import sys
import sysconfig


def main() -> None:
    expected = sys.argv[1]
    actual = ".".join(map(str, sys.version_info[:2]))
    print(
        f"Matrix Python {expected}; runtime {sys.version}; executable {sys.executable}; SOABI {sysconfig.get_config_var('SOABI')}"
    )
    if actual != expected:
        raise SystemExit(f"Interpreter mismatch: expected {expected}, got {actual}")


if __name__ == "__main__":
    main()
