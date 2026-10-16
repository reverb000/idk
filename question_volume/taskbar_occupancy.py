"""Read taskbar geometry without modifying Explorer or its controls.

UI Automation belongs to the scanner thread, never the mouse hook or overlay
message pump.  A missing, incomplete, or unsupported accessibility tree is not
evidence of empty space: callers must hide the overlay for unreliable snapshots.
Windows and COM imports are deliberately deferred so the policy is testable on
other platforms.
"""

from dataclasses import dataclass
import ctypes
import math
import sys
import threading
import time

from .taskbar_layout import Rect, TaskbarSnapshot


MAX_UIA_ELEMENTS = 1024
MAX_NATIVE_CHILDREN = 2048
MAX_UIA_SCAN_SECONDS = 0.75
MIN_BUTTON_DIP = 48

# Native UI Automation control type IDs, independent of generated COM modules.
BUTTON = 50000
IMAGE = 50006
LIST_ITEM = 50007
TEXT = 50020
CUSTOM = 50025
GROUP = 50026
WINDOW = 50032
PANE = 50033
INTERACTIVE_TYPES = frozenset({
    BUTTON, 50001, 50002, 50003, 50004, 50005, LIST_ITEM, 50009, 50011,
    50013, 50014, 50015, 50016, 50019, 50024, 50027, 50029, 50031, 50035,
})
MEANINGFUL_TYPES = INTERACTIVE_TYPES | {IMAGE, TEXT, CUSTOM}

# Only full-taskbar layout wrappers may be discarded. Unknown containers are
# protected too, even when that conservatively leaves no space for an answer.
_STRUCTURAL_CLASSES = frozenset(name.casefold() for name in (
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Windows.UI.Composition.DesktopWindowContentBridge",
    "Windows.UI.Core.CoreWindow", "DesktopWindowXamlSource",
    "XamlExplorerHostIslandWindow", "Taskbar.TaskbarFrame",
    "Taskbar.TaskbarFrameAutomationPeer", "Taskbar.TaskbarBackground",
))
_STRUCTURAL_IDS = frozenset(name.casefold() for name in (
    "TaskbarFrame", "TaskbarRoot", "RootGrid", "LayoutRoot",
))
_STRUCTURAL_TYPES = frozenset({PANE, WINDOW, GROUP, CUSTOM})
_APP_BUTTON_CLASSES = frozenset(name.casefold() for name in (
    "Taskbar.TaskListButton", "Taskbar.TaskListLabeledButton",
    "Taskbar.TaskListButtonAutomationPeer", "Taskbar.TaskListLabeledButtonAutomationPeer",
    "MSTaskListWClass",
))
_NATIVE_PROTECTED_CLASSES = frozenset(name.casefold() for name in (
    "TrayNotifyWnd", "TrayClockWClass", "TrayShowDesktopButtonWClass",
    "SysPager", "ToolbarWindow32", "Start", "StartButton", "SearchBox",
    "SearchBoxButton", "SearchUI", "CortanaButton", "TrayButton",
))


@dataclass(frozen=True)
class AutomationNode:
    bounds: Rect
    control_type: int
    class_name: str = ""
    automation_id: str = ""
    offscreen: bool = False


def _valid(rect):
    values = (rect.left, rect.top, rect.right, rect.bottom)
    return all(math.isfinite(value) for value in values) and (
        rect.right > rect.left and rect.bottom > rect.top
    )


def _clip(rect, bounds):
    clipped = Rect(max(rect.left, bounds.left), max(rect.top, bounds.top),
                   min(rect.right, bounds.right), min(rect.bottom, bounds.bottom))
    return clipped if _valid(clipped) else None


def occupied_regions(bounds, nodes, *, dpi=96, native_occupied=()):
    """Return (protected rectangles, reliable) from a complete UIA result.

    The native rectangles supplement, but never replace, UIA evidence. Requiring
    two distinct meaningful descendants and a recognizable application button
    rejects unsupported trees exposing only Start/Search or accessibility proxies.
    English display names are not used to recognize controls.
    """
    if not _valid(bounds) or not 48 <= dpi <= 960 or len(nodes) > MAX_UIA_ELEMENTS:
        return (), False
    protected = []
    meaningful = set()
    has_interactive = False
    has_app_button = False
    complete = True
    for node in nodes:
        if node.offscreen:
            continue
        if not _valid(node.bounds):
            complete = False
            continue
        clipped = _clip(node.bounds, bounds)
        if clipped is None:
            continue
        if not isinstance(node.control_type, int) or not 50000 <= node.control_type <= 50040:
            complete = False
            continue
        structural = (
            clipped == bounds
            and node.control_type in _STRUCTURAL_TYPES
            and (node.class_name.casefold() in _STRUCTURAL_CLASSES
                 or node.automation_id.casefold() in _STRUCTURAL_IDS)
        )
        if structural:
            continue
        if node.control_type in MEANINGFUL_TYPES:
            meaningful.add((clipped.left, clipped.top, clipped.right, clipped.bottom))
        if node.control_type in INTERACTIVE_TYPES:
            has_interactive = True
            if (node.class_name.casefold() in _APP_BUTTON_CLASSES
                    or node.automation_id.casefold().startswith("appid:")):
                has_app_button = True
            # Some providers expose only the centered icon. Protect at least a
            # full standard button width rather than claiming its hit area free.
            half_width = max(clipped.right - clipped.left, MIN_BUTTON_DIP * dpi / 96) / 2
            center = (clipped.left + clipped.right) / 2
            clipped = _clip(Rect(math.floor(center - half_width), clipped.top,
                                 math.ceil(center + half_width), clipped.bottom), bounds)
        protected.append(clipped)
    for rect in native_occupied:
        if not _valid(rect):
            complete = False
            continue
        clipped = _clip(rect, bounds)
        if clipped is not None:
            protected.append(clipped)
    reliable = complete and len(meaningful) >= 2 and has_interactive and has_app_button
    return tuple(dict.fromkeys(protected)), reliable


class WindowsTaskbarInspector:
    """Owner-thread-only taskbar observer used by the background scanner.

    ``_native`` and ``_automation`` are private dependency injection points for
    deterministic tests; production creates both on this same scanning thread.
    """

    def __init__(self, *, _native=None, _automation=None, _clock=time.monotonic):
        self._owner = threading.get_ident()
        self._closed = False
        self._clock = _clock
        if sys.platform != "win32" and (_native is None or _automation is None):
            raise RuntimeError("Taskbar inspection requires Windows 10/11 and UI Automation.")
        self._native = _native if _native is not None else _WindowsNative()
        self._automation = _automation if _automation is not None else _WindowsAutomation()

    def _check_thread(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("Taskbar inspector must run on its creating scanner thread.")
        if self._closed:
            raise RuntimeError("Taskbar inspector is closed.")

    def scan(self):
        self._check_thread()
        observed_at = self._clock()
        try:
            windows = self._native.taskbars()
        except Exception:
            return ()  # Explorer may be restarting. Previous observations expire.
        snapshots = []
        for hwnd in windows:
            bounds = Rect(0, 0, 0, 0)
            dpi = 96
            try:
                bounds = self._native.bounds(hwnd)
                dpi = self._native.dpi(hwnd)
                monitor = self._native.monitor_bounds(hwnd)
                visible = (
                    self._native.is_visible(hwnd) and _valid(bounds)
                    and _valid(monitor) and _clip(bounds, monitor) == bounds
                    and not self._native.auto_hide_enabled()
                    and not self._native.fullscreen_covering(monitor)
                )
                if not visible:
                    snapshots.append(TaskbarSnapshot(bounds, (), dpi=dpi,
                        observed_at=observed_at, reliable=False, visible=False))
                    continue
                native = self._native.protected_children(hwnd)
                nodes = self._automation.descendants(hwnd)
                occupied, reliable = occupied_regions(bounds, nodes, dpi=dpi,
                                                       native_occupied=native)
                snapshots.append(TaskbarSnapshot(bounds, occupied, dpi=dpi,
                    observed_at=observed_at, reliable=reliable, visible=True))
            except Exception:
                # ElementNotAvailable, Explorer restart, a timeout, or a broken
                # provider must hide this taskbar's overlay, never guess a gap.
                snapshots.append(TaskbarSnapshot(bounds, (), dpi=dpi,
                    observed_at=observed_at, reliable=False, visible=False))
        return tuple(snapshots)

    def close(self):
        if self._closed:
            return
        self._check_thread()
        self._closed = True
        self._automation.close()


class _WindowsAutomation:
    def __init__(self):
        self._comtypes = None
        self._automation = None
        self._initializations = 0
        try:
            # comtypes initializes the first importing thread as a side effect.
            # Select MTA before that import; subsequent scanner threads explicitly
            # initialize themselves. Restore the process default afterwards.
            first_import = "comtypes" not in sys.modules
            missing = object()
            previous = getattr(sys, "coinit_flags", missing)
            try:
                sys.coinit_flags = 0
                import comtypes
                self._comtypes = comtypes
                if first_import:
                    self._initializations = 1
                import comtypes.client
            finally:
                if previous is missing:
                    del sys.coinit_flags
                else:
                    sys.coinit_flags = previous
            comtypes.CoInitializeEx(0)  # COINIT_MULTITHREADED
            self._initializations += 1
            module = comtypes.client.GetModule("UIAutomationCore.dll")
            self._automation = comtypes.client.CreateObject(
                getattr(module, "CUIAutomation8", module.CUIAutomation),
                interface=module.IUIAutomation)
            interface2 = getattr(module, "IUIAutomation2", None)
            if interface2 is not None:
                newer = self._automation.QueryInterface(interface2)
                newer.ConnectionTimeout = 250
                newer.TransactionTimeout = 500
            self._condition = self._automation.CreateTrueCondition()
        except Exception as error:
            self.close()
            raise RuntimeError("Windows UI Automation could not start; the taskbar overlay "
                               "cannot safely locate empty space. " + str(error)) from error

    def descendants(self, hwnd):
        started = time.monotonic()
        root = self._automation.ElementFromHandle(hwnd)
        if root.CurrentIsOffscreen:
            raise RuntimeError("The taskbar accessibility root is offscreen.")
        collection = root.FindAll(4, self._condition)  # TreeScope_Descendants
        length = collection.Length
        if length > MAX_UIA_ELEMENTS or length < 0:
            raise RuntimeError("Taskbar accessibility tree exceeds the safe scan limit.")
        result = []
        for index in range(length):
            if time.monotonic() - started > MAX_UIA_SCAN_SECONDS:
                raise RuntimeError("Taskbar accessibility scan exceeded its freshness deadline.")
            element = collection.GetElement(index)
            native_rect = element.CurrentBoundingRectangle
            # Native UIAutomationCore uses RECT, not the .NET Rect type.
            bounds = Rect(native_rect.left, native_rect.top,
                          native_rect.right, native_rect.bottom)
            result.append(AutomationNode(bounds, int(element.CurrentControlType),
                element.CurrentClassName or "", element.CurrentAutomationId or "",
                bool(element.CurrentIsOffscreen)))
        if time.monotonic() - started > MAX_UIA_SCAN_SECONDS:
            raise RuntimeError("Taskbar accessibility scan exceeded its freshness deadline.")
        return tuple(result)

    def close(self):
        self._condition = None
        self._automation = None
        while self._initializations:
            self._initializations -= 1
            self._comtypes.CoUninitialize()


_LONG = ctypes.c_int32
_DWORD = ctypes.c_uint32
_HWND = ctypes.c_void_p
_BOOL = ctypes.c_int32
_LPARAM = ctypes.c_ssize_t
_CALLBACK = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(_BOOL, _HWND, _LPARAM)


class _RECT(ctypes.Structure):
    _fields_ = [("left", _LONG), ("top", _LONG), ("right", _LONG), ("bottom", _LONG)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", _DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", _DWORD)]


class _APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", _DWORD), ("hWnd", _HWND), ("uCallbackMessage", _DWORD),
                ("uEdge", _DWORD), ("rc", _RECT), ("lParam", _LPARAM)]


def _bind(library, name, args, result):
    function = getattr(library, name)
    function.argtypes, function.restype = args, result
    return function


class _WindowsNative:
    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        _bind(self.user, "EnumWindows", [_CALLBACK, _LPARAM], _BOOL)
        _bind(self.user, "EnumChildWindows", [_HWND, _CALLBACK, _LPARAM], _BOOL)
        _bind(self.user, "GetClassNameW", [_HWND, ctypes.c_wchar_p, ctypes.c_int], ctypes.c_int)
        _bind(self.user, "GetWindowRect", [_HWND, ctypes.POINTER(_RECT)], _BOOL)
        _bind(self.user, "IsWindowVisible", [_HWND], _BOOL)
        _bind(self.user, "IsWindow", [_HWND], _BOOL)
        _bind(self.user, "GetDpiForWindow", [_HWND], _DWORD)
        _bind(self.user, "MonitorFromWindow", [_HWND, _DWORD], _HWND)
        _bind(self.user, "GetMonitorInfoW", [_HWND, ctypes.POINTER(_MONITORINFO)], _BOOL)
        _bind(self.user, "GetForegroundWindow", [], _HWND)
        _bind(self.shell, "SHAppBarMessage", [_DWORD, ctypes.POINTER(_APPBARDATA)], ctypes.c_size_t)

    def _class(self, hwnd):
        buffer = ctypes.create_unicode_buffer(256)
        if not self.user.GetClassNameW(hwnd, buffer, len(buffer)):
            raise OSError("Could not inspect a taskbar window class.")
        return buffer.value.casefold()

    def _enumerate(self, parent=None):
        result, failures = [], []

        @_CALLBACK
        def callback(hwnd, _):
            try:
                result.append((hwnd, self._class(hwnd)))
                if len(result) > MAX_NATIVE_CHILDREN:
                    raise RuntimeError("Native taskbar tree exceeds the safe scan limit.")
                return True
            except Exception as error:
                failures.append(error)
                return False

        if parent is None:
            if not self.user.EnumWindows(callback, 0) and not failures:
                raise OSError("Could not enumerate desktop windows.")
        else:
            # EnumChildWindows has no documented failure return value. Validate
            # the owner before and after to catch an Explorer restart instead.
            if not self.user.IsWindow(parent):
                raise OSError("Taskbar window disappeared.")
            self.user.EnumChildWindows(parent, callback, 0)
            if not self.user.IsWindow(parent):
                raise OSError("Taskbar window disappeared during inspection.")
        if failures:
            raise failures[0]
        return tuple(result)

    def taskbars(self):
        return tuple(hwnd for hwnd, name in self._enumerate()
                     if name in {"shell_traywnd", "shell_secondarytraywnd"})

    def bounds(self, hwnd):
        rect = _RECT()
        if not self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise OSError("Could not read a taskbar window rectangle.")
        return Rect(rect.left, rect.top, rect.right, rect.bottom)

    def dpi(self, hwnd):
        value = self.user.GetDpiForWindow(hwnd)
        if not value:
            raise OSError("Could not read taskbar DPI.")
        return int(value)

    def is_visible(self, hwnd):
        return bool(self.user.IsWindowVisible(hwnd))

    def monitor_bounds(self, hwnd):
        handle = self.user.MonitorFromWindow(hwnd, 0)  # MONITOR_DEFAULTTONULL
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not handle or not self.user.GetMonitorInfoW(handle, ctypes.byref(info)):
            raise OSError("Could not read the taskbar monitor.")
        rect = info.rcMonitor
        return Rect(rect.left, rect.top, rect.right, rect.bottom)

    def auto_hide_enabled(self):
        info = _APPBARDATA()
        info.cbSize = ctypes.sizeof(info)
        # Conservatively remain hidden whenever auto-hide is enabled, including
        # the short reveal animation. No taskbar settings are changed.
        return bool(self.shell.SHAppBarMessage(4, ctypes.byref(info)) & 1)

    def fullscreen_covering(self, monitor):
        hwnd = self.user.GetForegroundWindow()
        if not hwnd or not self.is_visible(hwnd):
            return False
        if self._class(hwnd) in {"progman", "workerw", "shell_traywnd", "shell_secondarytraywnd"}:
            return False
        rect = self.bounds(hwnd)
        return (rect.left <= monitor.left and rect.top <= monitor.top
                and rect.right >= monitor.right and rect.bottom >= monitor.bottom)

    def protected_children(self, hwnd):
        return tuple(self.bounds(child) for child, name in self._enumerate(hwnd)
                     if name in _NATIVE_PROTECTED_CLASSES and self.is_visible(child))
