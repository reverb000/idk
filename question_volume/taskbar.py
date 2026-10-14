"""An answer caption in the Windows taskbar, without a foreground window.

Windows must be configured to show taskbar button labels (combine = Never).
The system may visually shorten long captions; the complete text is retained.
No tray notification, volume control, or third-party GUI package is used.
"""

import ctypes
import queue
import sys
import threading
import uuid


# Use explicit Windows widths: ctypes.wintypes.LONG has the host C long width
# on non-Windows hosts, which would make the native-layout tests misleading.
BOOL = ctypes.c_int32
UINT = DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
HANDLE = HWND = HINSTANCE = HICON = HCURSOR = HBRUSH = ctypes.c_void_p
WPARAM = ctypes.c_size_t
LPARAM = LRESULT = ctypes.c_ssize_t
LPCWSTR = ctypes.c_wchar_p
WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    LRESULT, HWND, UINT, WPARAM, LPARAM
)

WM_DESTROY = 0x0002
WM_SIZE = 0x0005
WM_CLOSE = 0x0010
WM_MOUSEACTIVATE = 0x0021
WM_WINDOWPOSCHANGING = 0x0046
WM_SYSCOMMAND = 0x0112
WM_APP_UPDATE = 0x8001
SC_SIZE = 0xF000
SC_MOVE = 0xF010
SC_MINIMIZE = 0xF020
SC_MAXIMIZE = 0xF030
SC_RESTORE = 0xF120
SIZE_MINIMIZED = 1
SW_SHOWMINNOACTIVE = 7
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
MA_NOACTIVATE = 3
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_MINIMIZE = 0x20000000
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
IDI_APPLICATION = 32512


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", HWND), ("message", UINT), ("wParam", WPARAM),
        ("lParam", LPARAM), ("time", DWORD), ("pt", POINT), ("lPrivate", DWORD),
    ]


class WINDOWPOS(ctypes.Structure):
    _fields_ = [
        ("hwnd", HWND), ("hwndInsertAfter", HWND), ("x", ctypes.c_int32),
        ("y", ctypes.c_int32), ("cx", ctypes.c_int32), ("cy", ctypes.c_int32),
        ("flags", UINT),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", UINT), ("style", UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int32), ("cbWndExtra", ctypes.c_int32),
        ("hInstance", HINSTANCE), ("hIcon", HICON), ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH), ("lpszMenuName", LPCWSTR),
        ("lpszClassName", LPCWSTR), ("hIconSm", HICON),
    ]


def _windows_error(operation):
    code = ctypes.get_last_error()
    return OSError(code, f"{operation}: {ctypes.FormatError(code)}")


def _bind(library, name, argtypes, restype):
    function = getattr(library, name)
    function.argtypes = argtypes
    function.restype = restype
    return function


class _NativeTaskbarWindow:
    """All operations except wake() belong to the owning message-pump thread."""

    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        _bind(self.kernel32, "GetModuleHandleW", [LPCWSTR], HINSTANCE)
        _bind(self.shell32, "SetCurrentProcessExplicitAppUserModelID", [LPCWSTR], LONG)
        _bind(self.user32, "RegisterClassExW", [ctypes.POINTER(WNDCLASSEXW)], ctypes.c_uint16)
        _bind(self.user32, "UnregisterClassW", [LPCWSTR, HINSTANCE], BOOL)
        _bind(self.user32, "LoadIconW", [HINSTANCE, ctypes.c_void_p], HICON)
        _bind(self.user32, "CreateWindowExW", [
            DWORD, LPCWSTR, LPCWSTR, DWORD, ctypes.c_int32, ctypes.c_int32,
            ctypes.c_int32, ctypes.c_int32, HWND, HANDLE, HINSTANCE, ctypes.c_void_p,
        ], HWND)
        _bind(self.user32, "ShowWindow", [HWND, ctypes.c_int32], BOOL)
        _bind(self.user32, "SetWindowTextW", [HWND, LPCWSTR], BOOL)
        _bind(self.user32, "PostMessageW", [HWND, UINT, WPARAM, LPARAM], BOOL)
        _bind(self.user32, "GetMessageW", [ctypes.POINTER(MSG), HWND, UINT, UINT], BOOL)
        _bind(self.user32, "TranslateMessage", [ctypes.POINTER(MSG)], BOOL)
        _bind(self.user32, "DispatchMessageW", [ctypes.POINTER(MSG)], LRESULT)
        _bind(self.user32, "DefWindowProcW", [HWND, UINT, WPARAM, LPARAM], LRESULT)
        _bind(self.user32, "DestroyWindow", [HWND], BOOL)
        _bind(self.user32, "PostQuitMessage", [ctypes.c_int32], None)
        self._hwnd = None
        self._instance = None
        self._class_name = "QuestionTaskbar_" + uuid.uuid4().hex
        self._registered = False
        self._wndproc = None
        self._on_update = None
        self._callback_error = None

    def create(self, title, on_update):
        result = self.shell32.SetCurrentProcessExplicitAppUserModelID("QuestionTaskbar.Answers")
        if result < 0:
            raise OSError(f"Could not set the taskbar application identity (HRESULT {result:#x}).")
        self._instance = self.kernel32.GetModuleHandleW(None)
        if not self._instance:
            raise _windows_error("GetModuleHandleW")
        self._on_update = on_update
        # Retain this callback until the class has been unregistered. Native
        # Windows can invoke it throughout CreateWindow/DestroyWindow as well.
        self._wndproc = WNDPROC(self._window_proc)
        icon = self.user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
        if not icon:
            raise _windows_error("LoadIconW")
        window_class = WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = self._instance
        window_class.hIcon = window_class.hIconSm = icon
        window_class.lpszClassName = self._class_name
        if not self.user32.RegisterClassExW(ctypes.byref(window_class)):
            raise _windows_error("RegisterClassExW")
        self._registered = True
        self._hwnd = self.user32.CreateWindowExW(
            WS_EX_APPWINDOW | WS_EX_NOACTIVATE, self._class_name, title,
            WS_OVERLAPPEDWINDOW | WS_MINIMIZE, -32000, -32000, 160, 48,
            None, None, self._instance, None,
        )
        if not self._hwnd:
            raise _windows_error("CreateWindowExW")
        self.user32.ShowWindow(self._hwnd, SW_SHOWMINNOACTIVE)
        if self._callback_error:
            raise self._callback_error

    def _window_proc(self, hwnd, message, wparam, lparam):
        # ctypes callbacks must never let a Python exception cross into Win32.
        try:
            return self._handle_message(hwnd, message, wparam, lparam)
        except BaseException as error:
            self._callback_error = error
            self.user32.DestroyWindow(hwnd)
            self.user32.PostQuitMessage(1)
            return 0

    def _handle_message(self, hwnd, message, wparam, lparam):
        if message == WM_APP_UPDATE:
            self._on_update()
            return 0
        if message == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if message == WM_SYSCOMMAND and (wparam & 0xFFF0) in (
            SC_RESTORE, SC_MAXIMIZE, SC_MOVE, SC_SIZE,
        ):
            # Clicking the taskbar button must not restore a visible app window.
            return 0
        if message == WM_WINDOWPOSCHANGING and lparam:
            position = ctypes.cast(lparam, ctypes.POINTER(WINDOWPOS)).contents
            position.flags = (position.flags | SWP_NOACTIVATE) & ~SWP_NOMOVE
            position.x = position.y = -32000
            return 0
        if message == WM_SIZE and wparam != SIZE_MINIMIZED and self._hwnd:
            self.user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
            return 0
        if message == WM_CLOSE:
            self.user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            self._hwnd = None
            self.user32.PostQuitMessage(0)
            return 0
        return self.user32.DefWindowProcW(hwnd, message, wparam, lparam)

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

    def set_title(self, text):
        if not self._hwnd or not self.user32.SetWindowTextW(self._hwnd, text):
            raise _windows_error("SetWindowTextW")

    def stop(self):
        if self._hwnd:
            if not self.user32.DestroyWindow(self._hwnd):
                raise _windows_error("DestroyWindow")

    def close(self):
        self.stop()
        if self._registered:
            if not self.user32.UnregisterClassW(self._class_name, self._instance):
                raise _windows_error("UnregisterClassW")
            self._registered = False
        # LoadIconW returned a shared system icon: it must not be destroyed.
        self._wndproc = None
        self._on_update = None


class WindowsTaskbarDisplay:
    """Thread-safe caption updates for a minimized, non-activating taskbar button.

    start() waits until the button exists; show() only queues a caption update.
    close() stops the UI thread with a bounded wait. The display cannot restart
    after closing. Windows may truncate the caption to the available button width.
    """

    def __init__(self, ready_text="?", *, startup_timeout=5, shutdown_timeout=5,
                 _native_factory=None):
        if sys.platform != "win32" and _native_factory is None:
            raise RuntimeError("The taskbar answer display requires Windows 10/11.")
        self._validate(ready_text)
        if startup_timeout <= 0 or shutdown_timeout <= 0:
            raise ValueError("Taskbar startup and shutdown timeouts must be positive.")
        self._initial_text = ready_text
        self._factory = _native_factory or _NativeTaskbarWindow
        self._startup_timeout = startup_timeout
        self._shutdown_timeout = shutdown_timeout
        self._lock = threading.Lock()
        self._updates = queue.Queue()
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._thread = None
        self._native = None
        self._error = None
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
                self._thread = threading.Thread(
                    target=self._run, name="taskbar-answer-display", daemon=True,
                )
                self._thread.start()
        if not self._ready.wait(self._startup_timeout):
            try:
                self.close()
            except RuntimeError:
                pass
            raise RuntimeError("The Windows taskbar display did not start in time.")
        self.check()
        return self

    def _run(self):
        native = None
        try:
            native = self._factory()
            with self._lock:
                self._native = native
            native.create(self._initial_text, self._apply_updates)
            if self._stopping.is_set():
                native.stop()
            self._ready.set()
            if not self._stopping.is_set():
                native.run()
            if not self._stopping.is_set():
                raise RuntimeError("The Windows taskbar display stopped unexpectedly.")
        except BaseException as error:
            with self._lock:
                self._error = error
        finally:
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
        # Every show posts a wake-up, so draining the queue also safely coalesces
        # bursts without doing native work in the mouse-hook callback thread.
        latest = None
        while True:
            try:
                latest = self._updates.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self._native.set_title(latest)

    def _check_locked(self):
        if self._error is not None:
            raise RuntimeError(f"The taskbar display failed: {self._error}") from self._error
        if self._closed:
            raise RuntimeError("The taskbar display has been closed.")
        if not self._ready.is_set() or self._thread is None or not self._thread.is_alive():
            raise RuntimeError("The taskbar display is not running; call start() first.")

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
            thread, native = self._thread, self._native
        if native is not None:
            try:
                native.wake()
            except OSError:
                # A window already being destroyed no longer accepts messages.
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._shutdown_timeout)
            if thread.is_alive():
                raise RuntimeError("The Windows taskbar display did not stop in time.")

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()
