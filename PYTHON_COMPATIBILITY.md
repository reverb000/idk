# Exact Python version to install

Use **Python 3.14.8, standard Windows 64-bit build**:
<https://www.python.org/downloads/release/python-3148/>.
`setup.cmd` runs `py -3.14 install_windows.py` without a PowerShell script.
The optional `setup.ps1` also defaults to `py -3.14`; `.python-version` records
the exact tested patch. The existing pinned dependencies do not need to change.

Checked on October 14, 2026:

| Interpreter | Result | Windows dependency evidence |
| --- | --- | --- |
| CPython 3.14.8 | All 85 tests passed, including real Tesseract OCR. | All 15 direct/transitive packages resolve as Windows x64 wheels with the existing requirements. |
| CPython 3.15.0, latest stable | All 85 tests passed using Pillow 12.3.0 in a separate test environment. | Windows x64 wheels resolve after replacing Pillow 11.3.0 with 12.3.0, but comtypes 1.4.17 emits a Python 3.15 enum compatibility warning. |
| CPython 3.12.14 | All 85 tests passed in the earlier environment. | Retained as an optional setup target. |

These tests ran on Linux. Native Windows global mouse hooks, Credential
Manager, and live audio changes still require testing on your computer.
The tests exercise Windows adapters with mocks; successful package resolution
does not establish native runtime compatibility. The real Ollama model was
not run in these checks; use `--check-solver` to test it on your PC.

Python 3.15.0 is not selected by the installer because its audio dependency
still carries that upstream warning. This is a compatibility limitation to
investigate on Windows, not proof that the application fails on 3.15.

Release and package sources:

- Official CPython release tags:
  <https://github.com/python/cpython/releases/tag/v3.14.8> and
  <https://github.com/python/cpython/releases/tag/v3.15.0>.
- Pillow wheels: <https://pypi.org/project/pillow/11.3.0/> and
  <https://pypi.org/project/pillow/12.3.0/>.
- comtypes compatibility warning:
  <https://github.com/enthought/comtypes/issues/894> and
  <https://raw.githubusercontent.com/enthought/comtypes/main/CHANGES.txt>.

If `.venv` already contains Python 3.12, either run
`setup.ps1 -PythonVersion 3.12` or extract the updated program into a fresh
folder for 3.14. Setup preserves the existing environment rather than
overwriting it with binaries from a different Python version.
