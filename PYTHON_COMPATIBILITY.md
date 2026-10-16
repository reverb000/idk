# Exact Python version to install

Use **Python 3.14.8, standard Windows 64-bit build**:
<https://www.python.org/downloads/release/python-3148/>.
`setup.cmd` runs `py -3.14 install_windows.py` without a PowerShell script.
The optional `setup.ps1` also defaults to `py -3.14`; `.python-version` records
the recommended patch.

The taskbar edition retains Python 3.14 as its default installation target.
Python 3.12 remains an optional target for an existing installation. Earlier
interpreter checks also exercised CPython 3.15.0, but they preceded the taskbar
feature and do not establish native Windows compatibility for this edition.

The application no longer uses an audio-control package. `comtypes` remains
for creating Windows shortcuts during installation and reading taskbar
controls through Windows UI Automation. Earlier warnings about that package
on Python 3.15 are not evidence about taskbar display behavior.
Use the recommended 3.14 build unless you are testing another interpreter.

Automated checks run in Linux and exercise Windows adapters with mocks. Native
Windows global mouse hooks, native UI Automation, empty-gap overlay placement
and scrolling, click-through and focus behavior, and Credential Manager still
require validation on a Windows desktop. The display is a standalone overlay
with no taskbar app button and needs no taskbar label or grouping changes.
**Automatically hide the taskbar** must be off to display the overlay; enabling
it hides the overlay even while the taskbar is visible on hover. The program
does not change this setting automatically. Synthetic
geometry checks exercise placement decisions, but package resolution and
mocked tests do not establish native runtime compatibility. The real Ollama
model was not run in those checks; use
`--check-solver` to test it on your PC.

Release and package sources:

- Official CPython releases:
  <https://www.python.org/downloads/release/python-3148/> and
  <https://www.python.org/downloads/release/python-3150/>.
- Pillow: <https://pypi.org/project/pillow/>.
- comtypes: <https://pypi.org/project/comtypes/> and
  <https://github.com/enthought/comtypes/issues/894>.

If `.venv` already contains Python 3.12, either run
`py -3.12 install_windows.py`, use `setup.ps1 -PythonVersion 3.12`, or extract
the updated program into a fresh folder for 3.14. Setup preserves the existing
environment rather than overwriting it with binaries from a different Python
version.
