"""Keep native taskbar mouse actions out of the question-reading hook."""

import ctypes
from functools import lru_cache
import sys


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _HitTester:
    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.WindowFromPoint.argtypes = [_Point]
        self.user.WindowFromPoint.restype = ctypes.c_void_p
        self.user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.user.GetAncestor.restype = ctypes.c_void_p
        self.user.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int32]
        self.user.GetClassNameW.restype = ctypes.c_int32

    def contains(self, x, y):
        window = self.user.WindowFromPoint(_Point(x, y))
        if not window:
            return None
        root = self.user.GetAncestor(window, 2)  # GA_ROOT
        if not root:
            return None
        for handle in dict.fromkeys((root, window)):
            name = ctypes.create_unicode_buffer(256)
            if not self.user.GetClassNameW(handle, name, len(name)):
                return None
            value = name.value.casefold()
            if value in ("shell_traywnd", "shell_secondarytraywnd") or value.startswith("questiontaskbaroverlay_"):
                return True
        return False


@lru_cache(maxsize=1)
def _hit_tester():
    return _HitTester()


def taskbar_point(x, y):
    """True for native taskbar/overlay; None means preserve the mouse event.

    This uses only quick native window queries, never UI Automation or disk I/O.
    The non-Windows return supports the synthetic listener used by unit tests.
    """
    if sys.platform != "win32":
        return False
    try:
        return _hit_tester().contains(x, y)
    except (OSError, AttributeError):
        return None
