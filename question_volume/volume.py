import math
import sys


def answer_steps(answer: str) -> int:
    if not isinstance(answer, str) or len(answer) != 1 or not "A" <= answer <= "Z":
        raise ValueError("Answer must be a single uppercase choice label.")
    return ord(answer) - ord("A") + 1


def volume_target(level, steps, step_percent=1, direction="auto", next_sign=1):
    if not math.isfinite(level) or not 0 <= level <= 1:
        raise ValueError("Invalid current volume.")
    if type(steps) is not int or steps < 1:
        raise ValueError("Invalid volume step count.")
    if not math.isfinite(step_percent) or step_percent <= 0:
        raise ValueError("Invalid step size.")
    if direction not in ("auto", "up", "down") or next_sign not in (-1, 1):
        raise ValueError("Invalid direction.")
    change = steps * step_percent / 100
    signs = (next_sign, -next_sign) if direction == "auto" else ((1,) if direction == "up" else (-1,))
    for sign in signs:
        target = level + sign * change
        if -1e-9 <= target <= 1 + 1e-9:
            return max(0, min(1, target)), -sign
    # Never clip: a partial signal would encode the wrong answer.
    return None, next_sign


class WindowsVolume:
    """Use Core Audio directly, avoiding synthetic media keys and their flyout."""

    def __init__(self, step_percent=1, direction="auto"):
        if sys.platform != "win32":
            raise RuntimeError("Volume control requires Windows 10/11.")
        self.step_percent = step_percent
        self.direction = direction
        self.next_sign = 1

    def signal(self, answer):
        import comtypes
        from pycaw.pycaw import AudioUtilities

        comtypes.CoInitialize()
        device = endpoint = None
        try:
            # Resolve the current default output on each click (headphones can change).
            device = AudioUtilities.GetSpeakers()
            endpoint = device.EndpointVolume
            level = endpoint.GetMasterVolumeLevelScalar()
            target, next_sign = volume_target(
                level, answer_steps(answer), self.step_percent, self.direction, self.next_sign
            )
            if target is None:
                return None
            endpoint.SetMasterVolumeLevelScalar(target, None)
            actual = endpoint.GetMasterVolumeLevelScalar()
            # Some hardware endpoints quantize levels. Do not silently encode a different choice.
            if abs(actual - target) > 0.0025:
                endpoint.SetMasterVolumeLevelScalar(level, None)
                raise RuntimeError("The audio device cannot represent the requested volume step.")
            self.next_sign = next_sign
            return (actual - level) * 100
        finally:
            # Release native proxies while their apartment is still initialized.
            endpoint = None
            device = None
            comtypes.CoUninitialize()
