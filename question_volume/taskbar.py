"""Click-through answers over verified empty taskbar space, without app buttons.

Taskbar inspection runs on a separate COM thread. The window stays hidden when
inspection is unavailable, stale, or cannot find room clear of Windows controls.
"""

import ctypes
import queue
import sys
import threading
import time
import uuid

from .taskbar_layout import find_space

BOOL = ctypes.c_int32
UINT = DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
HANDLE = HWND = HINSTANCE = HBRUSH = HDC = HFONT = ctypes.c_void_p
WPARAM = UINT_PTR = ctypes.c_size_t
LPARAM = LRESULT = ctypes.c_ssize_t
LPCWSTR = ctypes.c_wchar_p
WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    LRESULT, HWND, UINT, WPARAM, LPARAM,
)

WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_CLOSE = 0x0010
WM_ERASEBKGND = 0x0014
WM_MOUSEACTIVATE = 0x0021
WM_WINDOWPOSCHANGING = 0x0046
WM_NCHITTEST = 0x0084
WM_TIMER = 0x0113
WM_APP_UPDATE = 0x8001
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
MA_NOACTIVATE = 3
HTTRANSPARENT = -1
WS_POPUP = 0x80000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000  # Explicitly excluded from our styles.
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
LWA_ALPHA = 2
TRANSPARENT = 1
TIMER_ID = 1
TIMER_MS = 100


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", LONG), ("cy", LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", HWND), ("message", UINT), ("wParam", WPARAM),
        ("lParam", LPARAM), ("time", DWORD), ("pt", POINT), ("lPrivate", DWORD),
    ]


class WINDOWPOS(ctypes.Structure):
    _fields_ = [
        ("hwnd", HWND), ("hwndInsertAfter", HWND), ("x", LONG), ("y", LONG),
        ("cx", LONG), ("cy", LONG), ("flags", UINT),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", HDC), ("fErase", BOOL), ("rcPaint", RECT), ("fRestore", BOOL),
        ("fIncUpdate", BOOL), ("rgbReserved", ctypes.c_ubyte * 32),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", UINT), ("style", UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", LONG), ("cbWndExtra", LONG), ("hInstance", HINSTANCE),
        ("hIcon", HANDLE), ("hCursor", HANDLE), ("hbrBackground", HBRUSH),
        ("lpszMenuName", LPCWSTR), ("lpszClassName", LPCWSTR), ("hIconSm", HANDLE),
    ]


def _windows_error(operation):
    code = ctypes.get_last_error()
    return OSError(code, f"{operation}: {ctypes.FormatError(code)}")


def _bind(library, name, argtypes, restype):
    function = getattr(library, name)
    function.argtypes = argtypes
    function.restype = restype
    return function


def _utf16_length(text):
    """Win32 text lengths count UTF-16 code units, including surrogate pairs."""
    return len(text.encode("utf-16-le")) // 2


def _single_line(text):
    # Retain the complete original caption; only line breaks change in painting.
    return " ".join(text.splitlines())


def _marquee_positions(text_width, viewport_width, elapsed, scale=1):
    """Return clipped drawing origins for continuous, bounded scrolling."""
    padding, gap = round(6 * scale), max(1, round(32 * scale))
    if text_width <= viewport_width - 2 * padding:
        return (padding,)
    period = text_width + gap
    offset = max(0, elapsed - 0.75) * 32 * scale % period
    return (padding - round(offset), padding - round(offset) + period)


class _NativeTaskbarWindow:
    """All methods except wake() belong to the message-pump thread."""

    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        _bind(self.kernel32, "GetModuleHandleW", [LPCWSTR], HINSTANCE)
        _bind(self.user32, "RegisterClassExW", [ctypes.POINTER(WNDCLASSEXW)], ctypes.c_uint16)
        _bind(self.user32, "UnregisterClassW", [LPCWSTR, HINSTANCE], BOOL)
        _bind(self.user32, "CreateWindowExW", [
            DWORD, LPCWSTR, LPCWSTR, DWORD, LONG, LONG, LONG, LONG,
            HWND, HANDLE, HINSTANCE, ctypes.c_void_p,
        ], HWND)
        for name in ("ShowWindow",):
            _bind(self.user32, name, [HWND, LONG], BOOL)
        _bind(self.user32, "SetWindowTextW", [HWND, LPCWSTR], BOOL)
        _bind(self.user32, "SetWindowPos", [HWND, HWND, LONG, LONG, LONG, LONG, UINT], BOOL)
        _bind(self.user32, "SetLayeredWindowAttributes", [HWND, DWORD, ctypes.c_ubyte, DWORD], BOOL)
        _bind(self.user32, "SetTimer", [HWND, UINT_PTR, UINT, ctypes.c_void_p], UINT_PTR)
        _bind(self.user32, "KillTimer", [HWND, UINT_PTR], BOOL)
        _bind(self.user32, "PostMessageW", [HWND, UINT, WPARAM, LPARAM], BOOL)
        _bind(self.user32, "GetMessageW", [ctypes.POINTER(MSG), HWND, UINT, UINT], BOOL)
        _bind(self.user32, "TranslateMessage", [ctypes.POINTER(MSG)], BOOL)
        _bind(self.user32, "DispatchMessageW", [ctypes.POINTER(MSG)], LRESULT)
        _bind(self.user32, "DefWindowProcW", [HWND, UINT, WPARAM, LPARAM], LRESULT)
        _bind(self.user32, "DestroyWindow", [HWND], BOOL)
        _bind(self.user32, "PostQuitMessage", [LONG], None)
        _bind(self.user32, "GetDC", [HWND], HDC)
        _bind(self.user32, "ReleaseDC", [HWND, HDC], LONG)
        _bind(self.user32, "GetClientRect", [HWND, ctypes.POINTER(RECT)], BOOL)
        _bind(self.user32, "InvalidateRect", [HWND, ctypes.POINTER(RECT), BOOL], BOOL)
        _bind(self.user32, "BeginPaint", [HWND, ctypes.POINTER(PAINTSTRUCT)], HDC)
        _bind(self.user32, "EndPaint", [HWND, ctypes.POINTER(PAINTSTRUCT)], BOOL)
        _bind(self.user32, "FillRect", [HDC, ctypes.POINTER(RECT), HBRUSH], LONG)
        _bind(self.gdi32, "CreateFontW", [LONG] * 5 + [DWORD] * 8 + [LPCWSTR], HFONT)
        _bind(self.gdi32, "CreateSolidBrush", [DWORD], HBRUSH)
        _bind(self.gdi32, "DeleteObject", [HANDLE], BOOL)
        _bind(self.gdi32, "SelectObject", [HDC, HANDLE], HANDLE)
        _bind(self.gdi32, "GetTextExtentPoint32W", [HDC, LPCWSTR, LONG, ctypes.POINTER(SIZE)], BOOL)
        _bind(self.gdi32, "SetTextColor", [HDC, DWORD], DWORD)
        _bind(self.gdi32, "SetBkMode", [HDC, LONG], LONG)
        _bind(self.gdi32, "SaveDC", [HDC], LONG)
        _bind(self.gdi32, "RestoreDC", [HDC, LONG], BOOL)
        _bind(self.gdi32, "IntersectClipRect", [HDC, LONG, LONG, LONG, LONG], LONG)
        _bind(self.gdi32, "TextOutW", [HDC, LONG, LONG, LPCWSTR, LONG], BOOL)
        self._hwnd = self._instance = None
        self._class_name = "QuestionTaskbarOverlay_" + uuid.uuid4().hex
        self._registered = False
        self._wndproc = self._on_update = self._callback_error = None
        self._base_font = self._font = self._brush = None
        self._dpi = 96
        self._title = self._paint_text = ""
        self._placement = None
        self._visible = False
        self._scrolling = False
        self._render_extent = None
        self._scroll_started = time.monotonic()
        self._timer = False

    def _new_font(self, dpi):
        font = self.gdi32.CreateFontW(
            -max(1, round(14 * dpi / 96)), 0, 0, 0, 500,
            0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI",
        )
        if not font:
            raise _windows_error("CreateFontW")
        return font

    def create(self, title, on_update):
        self._instance = self.kernel32.GetModuleHandleW(None)
        if not self._instance:
            raise _windows_error("GetModuleHandleW")
        self._on_update = on_update
        self._wndproc = WNDPROC(self._window_proc)
        window_class = WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = self._instance
        window_class.lpszClassName = self._class_name
        if not self.user32.RegisterClassExW(ctypes.byref(window_class)):
            raise _windows_error("RegisterClassExW")
        self._registered = True
        self._hwnd = self.user32.CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED | WS_EX_TRANSPARENT,
            self._class_name, title, WS_POPUP, 0, 0, 0, 0,
            None, None, self._instance, None,
        )
        if not self._hwnd:
            raise _windows_error("CreateWindowExW")
        if not self.user32.SetLayeredWindowAttributes(self._hwnd, 0, 235, LWA_ALPHA):
            raise _windows_error("SetLayeredWindowAttributes")
        self._base_font = self._font = self._new_font(96)
        self._brush = self.gdi32.CreateSolidBrush(0x202020)
        if not self._brush:
            raise _windows_error("CreateSolidBrush")
        self._title, self._paint_text = title, _single_line(title)
        if not self.user32.SetTimer(self._hwnd, TIMER_ID, TIMER_MS, None):
            raise _windows_error("SetTimer")
        self._timer = True
        # The window starts hidden and stays hidden until a verified safe gap exists.
        if self._callback_error:
            raise self._callback_error

    def _window_proc(self, hwnd, message, wparam, lparam):
        try:
            return self._handle_message(hwnd, message, wparam, lparam)
        except BaseException as error:
            self._callback_error = error
            self.user32.DestroyWindow(hwnd)
            self.user32.PostQuitMessage(1)
            return 0

    def _handle_message(self, hwnd, message, wparam, lparam):
        if message == WM_APP_UPDATE or (message == WM_TIMER and wparam == TIMER_ID):
            self._on_update()
            return 0
        if message == WM_NCHITTEST:
            return HTTRANSPARENT
        if message == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if message == WM_WINDOWPOSCHANGING and lparam:
            position = ctypes.cast(lparam, ctypes.POINTER(WINDOWPOS)).contents
            position.flags |= SWP_NOACTIVATE
            return 0
        if message == WM_ERASEBKGND:
            return 1
        if message == WM_PAINT:
            self._paint(hwnd)
            return 0
        if message == WM_CLOSE:
            self.user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            if self._timer:
                self.user32.KillTimer(hwnd, TIMER_ID)
                self._timer = False
            self._hwnd = None
            self._visible = False
            self.user32.PostQuitMessage(0)
            return 0
        return self.user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _extent(self, text, font):
        dc = self.user32.GetDC(self._hwnd)
        if not dc:
            raise _windows_error("GetDC")
        previous = self.gdi32.SelectObject(dc, font)
        try:
            size = SIZE()
            line = _single_line(text)
            if not self.gdi32.GetTextExtentPoint32W(dc, line, _utf16_length(line), ctypes.byref(size)):
                raise _windows_error("GetTextExtentPoint32W")
            return size.cx, size.cy
        finally:
            self.gdi32.SelectObject(dc, previous)
            self.user32.ReleaseDC(self._hwnd, dc)

    def measure(self, text):
        width, height = self._extent(text, self._base_font)
        return float(width), float(max(18, height))

    def set_title(self, text):
        if not self._hwnd or not self.user32.SetWindowTextW(self._hwnd, text):
            raise _windows_error("SetWindowTextW")
        self._title, self._paint_text = text, _single_line(text)
        self._render_extent = None
        self._scroll_started = time.monotonic()
        self.user32.InvalidateRect(self._hwnd, None, False)

    def render(self, placement):
        if placement is None:
            if self._visible:
                self.user32.ShowWindow(self._hwnd, SW_HIDE)
            self._visible = False
            self._placement = None
            return
        if placement.dpi != self._dpi:
            new_font = self._base_font if placement.dpi == 96 else self._new_font(placement.dpi)
            if self._font and self._font != self._base_font:
                self.gdi32.DeleteObject(self._font)
            self._font, self._dpi = new_font, placement.dpi
            self._render_extent = None
        bounds = placement.bounds
        if self._render_extent is None:
            self._render_extent = self._extent(self._paint_text, self._font)
        # Integer font heights and glyph hinting can produce slightly different
        # metrics at fractional scaling. Scroll any actual overflow as well.
        available_width = bounds.right - bounds.left - 2 * round(6 * self._dpi / 96)
        self._scrolling = placement.scroll or self._render_extent[0] > available_width
        changed = self._placement != placement
        if changed:
            self._scroll_started = time.monotonic()
        self._placement = placement
        # Only our independent tool window is positioned. Explorer and every
        # application button keep their original bounds and ordering.
        if not self.user32.SetWindowPos(
            self._hwnd, HWND(-1), bounds.left, bounds.top,
            bounds.right - bounds.left, bounds.bottom - bounds.top,
            SWP_NOACTIVATE | SWP_NOOWNERZORDER,
        ):
            raise _windows_error("SetWindowPos")
        if not self._visible:
            self.user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
            self._visible = True
        if changed or self._scrolling:
            self.user32.InvalidateRect(self._hwnd, None, False)

    def _paint(self, hwnd):
        paint = PAINTSTRUCT()
        dc = self.user32.BeginPaint(hwnd, ctypes.byref(paint))
        if not dc:
            raise _windows_error("BeginPaint")
        try:
            rect = RECT()
            if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
                raise _windows_error("GetClientRect")
            saved = self.gdi32.SaveDC(dc)
            previous = self.gdi32.SelectObject(dc, self._font)
            try:
                self.gdi32.IntersectClipRect(dc, 0, 0, rect.right, rect.bottom)
                self.user32.FillRect(dc, ctypes.byref(rect), self._brush)
                self.gdi32.SetBkMode(dc, TRANSPARENT)
                self.gdi32.SetTextColor(dc, 0xFFFFFF)
                size = SIZE()
                count = _utf16_length(self._paint_text)
                if not self.gdi32.GetTextExtentPoint32W(dc, self._paint_text, count, ctypes.byref(size)):
                    raise _windows_error("GetTextExtentPoint32W")
                scale = self._dpi / 96
                scroll = self._placement is not None and self._scrolling
                positions = _marquee_positions(
                    size.cx, rect.right, time.monotonic() - self._scroll_started, scale,
                ) if scroll else (round(6 * scale),)
                y = max(0, (rect.bottom - size.cy) // 2)
                for x in positions:
                    if x < rect.right and x + size.cx > 0:
                        if not self.gdi32.TextOutW(dc, x, y, self._paint_text, count):
                            raise _windows_error("TextOutW")
            finally:
                self.gdi32.SelectObject(dc, previous)
                if saved:
                    self.gdi32.RestoreDC(dc, saved)
        finally:
            self.user32.EndPaint(hwnd, ctypes.byref(paint))

    def run(self):
        message = MSG()
        while True:
            result = self.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result == -1:
                raise _windows_error("GetMessageW")
            if result == 0:
                break
            self.user32.TranslateMessage(ctypes.byref(message))
            self.user32.DispatchMessageW(ctypes.byref(message))
            if self._callback_error:
                raise self._callback_error
        if self._callback_error:
            raise self._callback_error

    def wake(self):
        hwnd = self._hwnd
        if hwnd and not self.user32.PostMessageW(hwnd, WM_APP_UPDATE, 0, 0):
            raise _windows_error("PostMessageW")

    def stop(self):
        if self._hwnd:
            if self._timer:
                self.user32.KillTimer(self._hwnd, TIMER_ID)
                self._timer = False
            if not self.user32.DestroyWindow(self._hwnd):
                raise _windows_error("DestroyWindow")

    def close(self):
        self.stop()
        if self._registered:
            if not self.user32.UnregisterClassW(self._class_name, self._instance):
                raise _windows_error("UnregisterClassW")
            self._registered = False
        for handle in (self._font if self._font != self._base_font else None,
                       self._base_font, self._brush):
            if handle:
                self.gdi32.DeleteObject(handle)
        self._font = self._base_font = self._brush = None
        self._wndproc = self._on_update = None


class WindowsTaskbarDisplay:
    """Thread-safe text updates to a non-activating, click-through taskbar overlay.

    One thread scans Windows controls; another owns the window and checks cached
    geometry every 100 ms. Full text remains available for scrolling and storage.
    No taskbar button, tray icon, or Windows setting is created or changed.
    """

    def __init__(self, ready_text="?", *, startup_timeout=20, shutdown_timeout=5,
                 _native_factory=None, _inspector_factory=None):
        if sys.platform != "win32" and _native_factory is None:
            raise RuntimeError("The taskbar answer display requires Windows 10/11.")
        self._validate(ready_text)
        if startup_timeout <= 0 or shutdown_timeout <= 0:
            raise ValueError("Taskbar startup and shutdown timeouts must be positive.")
        if _inspector_factory is None:
            from .taskbar_occupancy import WindowsTaskbarInspector
            _inspector_factory = WindowsTaskbarInspector
        self._initial_text = ready_text
        self._factory = _native_factory or _NativeTaskbarWindow
        self._inspector_factory = _inspector_factory
        self._startup_timeout = startup_timeout
        self._shutdown_timeout = shutdown_timeout
        self._lock = threading.Lock()
        self._updates = queue.Queue()
        self._ready = threading.Event()
        self._scan_ready = threading.Event()
        self._stopping = threading.Event()
        self._thread = self._scanner_thread = self._native = None
        self._snapshots = ()
        self._scan_init_error = self._scan_error = self._error = None
        self._measurement = None
        self._closed = False

    @staticmethod
    def _validate(text):
        if not isinstance(text, str) or not text or "\x00" in text:
            raise ValueError("A taskbar caption must be nonempty text without null characters.")

    def start(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("The taskbar display has been closed.")
            if self._thread is None:
                self._scanner_thread = threading.Thread(
                    target=self._scan, name="taskbar-control-inspector", daemon=True,
                )
                self._thread = threading.Thread(
                    target=self._run, name="taskbar-answer-display", daemon=True,
                )
                self._scanner_thread.start()
                self._thread.start()
        if not self._ready.wait(self._startup_timeout):
            try:
                self.close()
            except RuntimeError:
                pass
            raise RuntimeError("The Windows taskbar display did not start in time.")
        self.check()
        return self

    def _scan(self):
        inspector = None
        try:
            inspector = self._inspector_factory()
            self._scan_ready.set()
            while not self._stopping.is_set():
                try:
                    snapshots = tuple(inspector.scan())
                    with self._lock:
                        self._snapshots, self._scan_error = snapshots, None
                except Exception as error:
                    # A temporarily busy Explorer must never leave stale text
                    # covering controls. Retry on the next inspection cycle.
                    with self._lock:
                        self._snapshots, self._scan_error = (), error
                self._stopping.wait(0.2)
        except BaseException as error:
            with self._lock:
                self._snapshots = ()
                if inspector is None:
                    self._scan_init_error = error
                else:
                    self._scan_error = error
        finally:
            if inspector is not None:
                try:
                    inspector.close()
                except BaseException as error:
                    with self._lock:
                        self._scan_error = error
            self._scan_ready.set()

    def _run(self):
        native = None
        try:
            while not self._scan_ready.wait(0.05):
                if self._stopping.is_set():
                    return
            if self._scan_init_error is not None:
                raise RuntimeError(f"Windows taskbar inspection could not start: {self._scan_init_error}")
            if self._stopping.is_set():
                return
            native = self._factory()
            with self._lock:
                self._native = native
            native.create(self._initial_text, self._apply_updates)
            self._measurement = native.measure(self._initial_text)
            if self._stopping.is_set():
                native.stop()
            else:
                self._apply_updates()
            self._ready.set()
            if not self._stopping.is_set():
                native.run()
            if not self._stopping.is_set():
                raise RuntimeError("The Windows taskbar display stopped unexpectedly.")
        except BaseException as error:
            with self._lock:
                self._error = error
        finally:
            self._stopping.set()
            if native is not None:
                try:
                    native.close()
                except BaseException as error:
                    with self._lock:
                        if self._error is None:
                            self._error = error
            self._ready.set()

    def _apply_updates(self):
        if self._stopping.is_set():
            self._native.stop()
            return
        latest = None
        while True:
            try:
                latest = self._updates.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self._native.set_title(latest)
            self._measurement = self._native.measure(latest)
        with self._lock:
            snapshots = self._snapshots
        placement = find_space(snapshots, *self._measurement, now=time.monotonic())
        self._native.render(placement)

    def _check_locked(self):
        if self._error is not None:
            raise RuntimeError(f"The taskbar display failed: {self._error}") from self._error
        if self._closed:
            raise RuntimeError("The taskbar display has been closed.")
        if not self._ready.is_set() or self._thread is None or not self._thread.is_alive():
            raise RuntimeError("The taskbar display is not running; call start() first.")
        if self._scanner_thread is None or not self._scanner_thread.is_alive():
            raise RuntimeError("The Windows taskbar inspection thread stopped unexpectedly.")

    def check(self):
        with self._lock:
            self._check_locked()

    def show(self, text):
        self._validate(text)
        with self._lock:
            self._check_locked()
            self._updates.put_nowait(text)
            native = self._native
        native.wake()

    def clear(self, status="?"):
        self.show(status)

    def close(self):
        with self._lock:
            self._closed = True
            self._stopping.set()
            threads, native = (self._thread, self._scanner_thread), self._native
        if native is not None:
            try:
                native.wake()
            except OSError:
                pass
        deadline = time.monotonic() + self._shutdown_timeout
        unfinished = []
        for thread in threads:
            if thread is not None and thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))
                if thread.is_alive():
                    unfinished.append(thread.name)
        if unfinished:
            raise RuntimeError("The Windows taskbar display did not stop in time.")

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()
