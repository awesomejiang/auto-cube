import ctypes
import os
import random
import re
import shutil
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageGrab, ImageOps
import pytesseract

KEY_INTERVAL = 0.20
KEY_JITTER = 0.04
TIMEOUT = 20.0
REF_WIDTH, REF_HEIGHT = 2048, 1111
REGIONS = {
    "dialog": (680, 340, 1370, 735),
    "left": (695, 711, 960, 761),
    "right": (1080, 711, 1355, 761),
    "reset": (820, 938, 1230, 1080),
    "cost": (1005, 1003, 1085, 1034),
    "owned": (1100, 1047, 1214, 1078),
}
FAILURE = "failure"
RESULT = "result"


def classify(dialog, reset, modal=False):
    dialog = " ".join(re.findall(r"[a-z0-9]+", dialog.lower()))
    reset = " ".join(re.findall(r"[a-z0-9]+", reset.lower()))
    if "please try again later" in dialog:
        return FAILURE
    if all(word in dialog for word in ("potential", "one more time", "confirm", "cancel")):
        return "confirm1"
    if all(word in dialog for word in ("relevant item", "used item", "quanah crystal",
                                      "reset potential", "confirm", "cancel")):
        return "confirm2"
    if not modal and "materials owned" in reset:
        return RESULT
    return None


def parse_number(value):
    value = value.strip().replace("−", "-")
    if not re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", value):
        raise ValueError(value)
    return int(value.replace(",", ""))


def ocr(frame, name, numeric=False, threshold=None):
    x1, y1, x2, y2 = REGIONS[name]
    box = (round(x1 * frame.width / REF_WIDTH), round(y1 * frame.height / REF_HEIGHT),
           round(x2 * frame.width / REF_WIDTH), round(y2 * frame.height / REF_HEIGHT))
    crop = ImageOps.grayscale(frame.crop(box))
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.Resampling.LANCZOS)
    crop = ImageOps.invert(ImageOps.autocontrast(crop))
    if threshold is not None:
        crop = crop.point(lambda pixel: 255 if pixel >= threshold else 0)
    config = "--psm 7 -c tessedit_char_whitelist=0123456789,+-" if numeric else "--psm 11"
    return pytesseract.image_to_string(crop, lang="eng", config=config, timeout=2)


def has_teal_dialog(frame):
    for y in range(360, 661, 10):
        matches = 0
        for x in range(800, 1250, 25):
            r, g, b = frame.getpixel((x * frame.width // REF_WIDTH,
                                      y * frame.height // REF_HEIGHT))[:3]
            matches += g - r >= 65 and b - r >= 75 and abs(b - g) <= 65
        if matches >= 16:
            return True
    return False


def numbers(frame):
    values = []
    for name in ("left", "right", "cost", "owned"):
        first = parse_number(ocr(frame, name, numeric=True))
        second = parse_number(ocr(frame, name, numeric=True, threshold=150))
        if first != second:
            raise ValueError(name)
        values.append(first)
    left, right, cost, owned = values
    if cost <= 0 or owned < 0:
        raise ValueError("材料读数异常")
    return left, right, cost, owned


class Window:
    def __init__(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        try:
            user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            user.SetProcessDPIAware()
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        user.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
        user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        self.user = user
        self.handle = user.GetForegroundWindow()
        title = ctypes.create_unicode_buffer(512)
        user.GetWindowTextW(self.handle, title, len(title))
        if not self.handle or not title.value:
            raise RuntimeError("没有读到前台窗口标题")
        print(f"已锁定窗口 {title.value}", flush=True)

    def check(self):
        if self.user.GetAsyncKeyState(0x77) & 0x8000:
            raise RuntimeError("已按 F8 停止")
        if self.user.GetForegroundWindow() != self.handle:
            raise RuntimeError("游戏窗口已失去焦点")

    def pause(self, seconds):
        end = time.monotonic() + seconds
        while True:
            self.check()
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))

    def screenshot(self):
        self.check()
        rect = wintypes.RECT()
        origin = wintypes.POINT()
        if not (self.user.GetClientRect(self.handle, ctypes.byref(rect))
                and self.user.ClientToScreen(self.handle, ctypes.byref(origin))):
            raise RuntimeError("无法读取窗口位置")
        if rect.right < 640 or rect.bottom < 480:
            raise RuntimeError("窗口尺寸过小")
        return ImageGrab.grab((origin.x, origin.y, origin.x + rect.right,
                               origin.y + rect.bottom), all_screens=True)

    def key(self, vk):
        self.check()
        self.user.keybd_event(vk, 0, 0, 0)
        time.sleep(0.06)
        self.user.keybd_event(vk, 0, 2, 0)
        self.pause(KEY_INTERVAL + random.uniform(-KEY_JITTER, KEY_JITTER))


def wait_for(window, expected, *, spent_from=None, allow_failure=True):
    deadline = time.monotonic() + TIMEOUT
    previous = None
    while time.monotonic() < deadline:
        frame = window.screenshot()
        state = classify(ocr(frame, "dialog"), ocr(frame, "reset"), has_teal_dialog(frame))
        observed = None
        if state == FAILURE and allow_failure:
            observed = (state, None)
        elif state == expected and state != RESULT:
            observed = (state, None)
        elif state == RESULT and expected == RESULT:
            try:
                values = numbers(frame)
                if spent_from is None or values[3] == spent_from - values[2]:
                    observed = (state, values)
            except ValueError:
                pass
        if observed is not None and observed == previous:
            return observed
        previous = observed
        window.pause(0.25)
    raise RuntimeError(f"等待 {expected} 超时，界面或读数未通过校验")


def play_stop_sound(frequencies):
    import winsound

    try:
        for frequency in frequencies:
            winsound.Beep(frequency, 160)
    except RuntimeError:
        pass


def run(window, preview=False):
    state, values = wait_for(window, RESULT, allow_failure=False)
    while True:
        left, right, cost, owned = values
        print(f"左 CP {left:,} | 右 CP {right:,} | 材料 {owned:,} | 每次 {cost}", flush=True)
        if preview:
            return "试读结束"
        if right > left:
            play_stop_sound((660, 880))
            return "右侧 CP 更高，请在游戏中选择 AFTER"
        if owned < cost:
            play_stop_sound((440, 330))
            return "材料不足"
        for expected in ("confirm1", "confirm2", RESULT):
            window.key(0x20)
            spent_from = owned if expected == RESULT else None
            state, new_values = wait_for(window, expected, spent_from=spent_from)
            if state == FAILURE:
                print("服务器提示稍后重试，正在关闭提示框", flush=True)
                window.pause(KEY_INTERVAL + random.uniform(-KEY_JITTER, KEY_JITTER))
                window.key(0x0D)
                _, values = wait_for(window, RESULT, allow_failure=False)
                break
            if state == RESULT:
                values = new_values


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("本脚本需要 Windows")
    bundled_ocr = Path(__file__).resolve().parent / "ocr"
    bundled_exe = bundled_ocr / "tesseract.exe"
    if bundled_exe.is_file():
        tesseract = str(bundled_exe)
        os.environ["TESSDATA_PREFIX"] = str(bundled_ocr / "tessdata")
    else:
        tesseract = shutil.which("tesseract")
    if not tesseract:
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe"
        if candidate.is_file():
            tesseract = str(candidate)
    if not tesseract:
        raise SystemExit("找不到 Tesseract OCR，请安装并加入 PATH")
    pytesseract.pytesseract.tesseract_cmd = tesseract
    input("请先打开魔方结果页。按回车后有 5 秒切回游戏窗口……")
    time.sleep(5)
    try:
        print(run(Window(), args.preview))
    except RuntimeError as error:
        print(f"已停止。{error}")


if __name__ == "__main__":
    main()
