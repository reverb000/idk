"""Capture geometry is independent of desktop APIs for testing."""


def capture_region(monitors, cursor, width, height):
    x, y = cursor
    for monitor in monitors:
        left, top = monitor["left"], monitor["top"]
        right, bottom = left + monitor["width"], top + monitor["height"]
        if left <= x < right and top <= y < bottom:
            width = min(width, monitor["width"])
            height = min(height, monitor["height"])
            crop_left = max(left, min(x - width // 2, right - width))
            crop_top = max(top, min(y - height // 2, bottom - height))
            return {"left": crop_left, "top": crop_top, "width": width, "height": height}
    raise ValueError("Cursor is not on a connected monitor.")


def screenshot_near_cursor(cursor, width, height):
    import mss
    from PIL import Image

    # mss handles thread-local native handles; create it on the hook thread.
    with mss.mss() as screen:
        region = capture_region(screen.monitors[1:], cursor, width, height)
        pixels = screen.grab(region)
        image = Image.frombytes("RGB", pixels.size, pixels.rgb)
    return image, (cursor[0] - region["left"], cursor[1] - region["top"])
