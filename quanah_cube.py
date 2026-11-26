import ctypes
import json
import os
import random
import re
import shutil
import sys
import threading
import time
import unicodedata
from ctypes import wintypes
from functools import lru_cache
from itertools import permutations, product
from pathlib import Path

from PIL import Image, ImageChops, ImageGrab, ImageOps, ImageStat
import pytesseract

KEY_INTERVAL = 0.20
KEY_JITTER = 0.04
TIMEOUT = 20.0
REF_WIDTH, REF_HEIGHT = 2048, 1111
REGIONS = {
    "dialog": (680, 340, 1370, 735),
    "left": (695, 711, 960, 761),
    "right": (1080, 711, 1355, 761),
    "after_lines": (1030, 500, 1435, 711),
    "reset": (820, 938, 1230, 1080),
}
# Offsets from the BEFORE label at a 488-pixel BEFORE–AFTER separation.
COMPARISON_REGIONS = {
    "left_cp_label": (78, 339, 400, 379),
    "right_cp_label": (573, 339, 902, 379),
    "left": (36, 381, 373, 434),
    "right": (558, 380, 826, 439),
    "after_1": (536, 138, 894, 177),
    "after_2": (536, 187, 894, 227),
    "after_3": (536, 235, 894, 275),
    "reset": (382, 672, 526, 724),
}
FAILURE = "failure"
RESULT = "result"
stop_requested = threading.Event()
COOLDOWN_LABEL = r"(?:all )?(?:skill )?cool\s*downs?(?: reduc(?:e|tion))?"
COOLDOWN_NAMES = ("cooldown", "cool down", "skill cooldown", "skill cooldowns",
                  "all skill cooldowns", "cooldown reduction", "skill cooldown reduction")
COOLDOWN_SECONDS = {"CD1": 1, "CD2": 2}
POTENTIAL_TYPES = {
    "STR": r"str",
    "DEX": r"dex",
    "INT": r"int",
    "LUK": r"luk",
    "ALL": r"all stats?",
    "ATT": r"att(?:ack(?: power)?)?",
    "MATT": r"m\.?\s?att(?:ack)?|magic (?:att(?:ack)?(?: power)?|power)",
    "HP": r"(?:max )?hp",
    "MP": r"(?:max )?mp",
    "DEF": r"def(?:ense)?",
    "BOSS": r"(?:damage (?:on|to) )?boss(?: monster(?:s)?(?: damage)?| damage)?",
    "IED": r"ignores? (?:monster |enemy )?def(?:ense)?",
    "DMG": r"(?:total )?damage",
    "CRIT": r"critical (?:hit )?rate",
    "CRITDMG": r"critical damage",
    "DROP": r"item drop rate",
    "MESO": r"mesos? (?:obtained|acquisition(?: rate)?)",
    "CD1": COOLDOWN_LABEL,
    "CD2": COOLDOWN_LABEL,
}
POTENTIAL_LABELS = {
    "ALL": ("all stat", "all stats"),
    "ATT": ("att", "attack", "attack power"),
    "MATT": ("matt", "m.att", "m.attack", "magic att", "magic attack", "magic attack power", "magic power"),
    "HP": ("hp", "max hp"),
    "MP": ("mp", "max mp"),
    "DEF": ("def", "defense"),
    "BOSS": ("boss damage", "boss monster damage", "damage on boss monsters", "damage to boss monsters"),
    "IED": ("ignore monster def", "ignores monster defense", "ignore enemy defense"),
    "DMG": ("damage", "total damage"),
    "CRIT": ("critical rate", "critical hit rate"),
    "CRITDMG": ("critical damage",),
    "DROP": ("item drop rate",),
    "MESO": ("meso obtained", "mesos obtained", "meso acquisition rate"),
    "CD1": COOLDOWN_NAMES,
    "CD2": COOLDOWN_NAMES,
}


def normalize_ocr_line(text):
    text = unicodedata.normalize("NFKC", text).replace("−", "-").replace("–", "-")
    return " ".join(text.split())


def parse_potential(text, *, expected_count=3):
    lines = []
    for raw_line in text.splitlines():
        line = normalize_ocr_line(raw_line).lower()
        clipped = re.search(r"(?:\.{2,3}|…)$", line)
        visible = line[:clipped.start()].strip() if clipped else line
        visible = re.sub(r"(?<=\d) +(?=\d)", "", visible)
        for kind, label in POTENTIAL_TYPES.items():
            match = re.fullmatch(rf"(?:{label})\s*:?\s*(?P<sign>[+-]?)\s*(?P<value>\d+)\s*(?P<unit>%|sec(?:onds?)?\.?)?", visible)
            if not match:
                continue
            sign, value, unit = match.group("sign", "value", "unit")
            if kind in ("CD1", "CD2") and (sign != "-" or value != kind[-1] or unit == "%"):
                continue
            if kind in ("ATT", "MATT") and unit != "%":
                kind = frozenset((kind,)) if clipped and unit is None else None
            lines.append((kind, line if clipped else visible))
            break
        else:
            # Keep recognizable non-target lines available for wildcard slots.
            if (re.fullmatch(r"[a-z][a-z ()/.'-]+\s*(?::\s*[+-]?|[+-])\s*\d+\s*%?", line)
                    or re.fullmatch(r"(?:enables? (?:the )?(?:use of )?)?[<«]?decent [a-z ]+[>»]?", line)
                    or re.fullmatch(r"\d+\s*% chance to ignore \d+\s*% damage", line)
                    or re.fullmatch(r"hp recovery [il]tems and skills\s*[+:]?\s*\d+\s*%", line)
                    or re.fullmatch(rf"{COOLDOWN_LABEL}\s*:?\s*[+-]?\s*\d+\s*sec(?:onds?)?\.?", visible)):
                lines.append((None, line))
            elif clipped and len(re.findall(r"[a-z]", visible)) >= 3:
                prefix = visible.rstrip(" :+-")
                candidates = frozenset(
                    kind for kind in POTENTIAL_TYPES
                    if any(label.startswith(prefix) for label in POTENTIAL_LABELS.get(kind, (kind.lower(),)))
                )
                kind = next(iter(candidates)) if len(candidates) == 1 else None
                complete_label = (kind and kind not in ("ATT", "MATT") and " " in prefix
                                  and prefix in POTENTIAL_LABELS.get(kind, ()))
                lines.append((kind if complete_label else candidates or None, line))
    return tuple(lines) if len(lines) == expected_count else ()


def matches_potential(targets, lines):
    if len(lines) != 3:
        return False
    if any(choices & COOLDOWN_SECONDS.keys() for choices in targets):
        return matches_with_cooldown(targets, lines)
    possible = False
    for order in permutations(lines):
        if all("*" in choices or kind in choices for choices, (kind, _) in zip(targets, order)):
            return True
        if all("*" in choices or (choices & kind if isinstance(kind, frozenset) else kind in choices)
               for choices, (kind, _) in zip(targets, order)):
            possible = True
    return None if possible else False


def matches_with_cooldown(targets, lines):
    kinds = tuple(kind for kind, _ in lines)
    possible = False
    for selection in product(*targets):
        required = tuple(kind for kind in selection if kind != "*" and kind not in COOLDOWN_SECONDS)
        seconds = sum(COOLDOWN_SECONDS.get(kind, 0) for kind in selection)
        for order in permutations(kinds):
            assigned = tuple(zip(required, order))
            remaining = order[len(required):]
            if (all(wanted == actual for wanted, actual in assigned)
                    and sum(COOLDOWN_SECONDS.get(kind, 0) for kind in remaining) >= seconds):
                return True
            if all(wanted in actual if isinstance(actual, frozenset) else wanted == actual
                   for wanted, actual in assigned):
                possible_seconds = sum(
                    max((COOLDOWN_SECONDS.get(candidate, 0) for candidate in kind), default=0)
                    if isinstance(kind, frozenset) else COOLDOWN_SECONDS.get(kind, 0)
                    for kind in remaining
                )
                possible |= possible_seconds >= seconds
    return None if possible else False


def parse_target(value):
    choices = frozenset(part.strip() for part in (value.strip().upper() or "*").split("|"))
    if "CD" in choices:
        choices = (choices - {"CD"}) | {"CD1", "CD2"}
    if choices == {"*"} or choices <= POTENTIAL_TYPES.keys():
        return choices
    raise ValueError("请输入词条类别，例如 STR|ALL，或留空。")


def configure_targets():
    print("可选词条 " + ", ".join(POTENTIAL_TYPES))
    print("每条条件可用 | 添加备选，例如 STR|ALL。留空表示任意。三条条件可换序。")
    targets = []
    for index in range(1, 4):
        while True:
            try:
                targets.append(parse_target(input(f"条件 {index} > ")))
                break
            except ValueError as error:
                print(error)
    return targets


def classify(dialog, reset, modal=False):
    dialog = " ".join(re.findall(r"[a-z0-9]+", dialog.lower()))
    dialog = re.sub(r"\b(relevant|used) [lt1]tem\b", r"\1 item", dialog)
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


def parse_number(value, *, allow_spacing=True):
    lines = value.strip().splitlines()
    if len(lines) == 1:
        normalized = normalize_ocr_line(lines[0])
        if not allow_spacing:
            if re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", normalized):
                return int(normalized.replace(",", ""))
            raise ValueError(value)
        normalized = normalized.replace(" ", "")
        if re.fullmatch(r"[+-]?[0-9]+(?:,[0-9]+)*", normalized):
            groups = normalized.split(",")
            digits_right = 0
            for group in reversed(groups[1:]):
                digits_right += len(group)
                if digits_right % 3:
                    break
            else:
                if len(groups) == 1 or len(groups[0].lstrip("+-")) <= 3:
                    return int("".join(groups))
    raise ValueError(value)


def ocr(frame, name, numeric=False, threshold=None, psm=11):
    x1, y1, x2, y2 = REGIONS[name]
    box = (round(x1 * frame.width / REF_WIDTH), round(y1 * frame.height / REF_HEIGHT),
           round(x2 * frame.width / REF_WIDTH), round(y2 * frame.height / REF_HEIGHT))
    return ocr_box(frame, box, numeric=numeric, threshold=threshold, psm=psm)


def ocr_box(frame, box, *, numeric=False, threshold=None, psm=11):
    crop = ImageOps.grayscale(frame.crop(box))
    return recognize_crop(crop.size, crop.tobytes(), numeric, threshold, psm)


@lru_cache(maxsize=64)
def recognize_crop(size, pixels, numeric, threshold, psm):
    crop = Image.frombytes("L", size, pixels)
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.Resampling.LANCZOS)
    crop = ImageOps.invert(ImageOps.autocontrast(crop))
    if threshold is not None:
        crop = crop.point(lambda pixel: 255 if pixel >= threshold else 0)
    config = "--psm 7 -c tessedit_char_whitelist=0123456789,+-" if numeric else f"--psm {psm}"
    return pytesseract.image_to_string(crop, lang="eng", config=config, timeout=2)


@lru_cache(maxsize=2)
def comparison_labels(size, pixels):
    labels = pytesseract.image_to_data(
        ImageOps.invert(Image.frombytes("L", size, pixels)), lang="eng", config="--psm 11",
        output_type=pytesseract.Output.DICT, timeout=3,
    )
    return labels


def find_comparison_pair(gray):
    labels = comparison_labels(gray.size, gray.tobytes())
    positions = {"BEFORE": [], "AFTER": []}
    for index, text in enumerate(labels["text"]):
        if text in positions and labels["conf"][index] >= 70:
            positions[text].append((labels["left"][index], labels["top"][index], labels["height"][index]))
    pairs = [
        (left, right) for left in positions["BEFORE"] for right in positions["AFTER"]
        if right[0] > left[0] and abs(left[1] - right[1]) <= max(left[2], right[2]) / 2
        and 14 * left[2] <= right[0] - left[0] <= 45 * left[2]
    ]
    return pairs[0] if len(pairs) == 1 else None


def potential_fingerprint(row):
    mask = ImageOps.grayscale(row).point([0] * 145 + [255] * 111, mode="1")
    box = mask.getbbox()
    if box is None:
        return None
    ink = mask.crop(box)
    return ink.size, ink.tobytes()


@lru_cache(maxsize=1)
def potential_templates():
    directory = Path(__file__).with_name("potential-templates")
    try:
        entries = json.loads((directory / "rows.json").read_text(encoding="utf-8"))
        templates = {}
        for filename, text in entries.items():
            with Image.open(directory / filename) as image:
                fingerprint = potential_fingerprint(image)
            if fingerprint is not None:
                templates.setdefault(fingerprint, text)
                if templates[fingerprint] != text:
                    templates[fingerprint] = None
        return templates
    except (OSError, ValueError):
        return {}


def panel_pixels(frame, x, y, scale):
    # Include Reset x1 so unchanged headers alone cannot signal a ready result.
    regions = ((-2, -2, 96, 23), (486, -2, 570, 23), COMPARISON_REGIONS["reset"])
    return tuple(
        ImageOps.grayscale(frame.crop((
            round(x + left * scale), round(y + top * scale),
            round(x + right * scale), round(y + bottom * scale),
        ))).tobytes()
        for left, top, right, bottom in regions
    )


def cached_comparison_position(frame, panel):
    saved = panel.get("position") if panel is not None else None
    if saved is not None:
        size, position, signatures = saved
        if size == frame.size and panel_pixels(frame, *position) == signatures:
            return position
    return None


def locate_comparison_position(frame):
    gray = ImageOps.grayscale(frame)
    x_offset, y_offset = round(frame.width * .24), round(frame.height * .32)
    title_band = gray.crop((x_offset, y_offset, round(frame.width * .76), round(frame.height * .43)))
    pair = find_comparison_pair(title_band)
    if pair is None:
        x_offset = y_offset = 0
        pair = find_comparison_pair(gray)
    if pair is None:
        raise ValueError("未能定位唯一的一组 BEFORE / AFTER 标题")
    (x, y, _), (after_x, _, _) = pair
    scale = (after_x - x) / 488
    return x + x_offset, y + y_offset, scale


def comparison_reading(frame, *, read_cp=True, targets=None, panel=None):
    position = cached_comparison_position(frame, panel)
    found_position = position is None
    if found_position:
        position = locate_comparison_position(frame)
    x, y, scale = position

    def region(name):
        left, top, right, bottom = COMPARISON_REGIONS[name]
        box = (round(x + left * scale), round(y + top * scale),
               round(x + right * scale), round(y + bottom * scale))
        if box[0] < 0 or box[1] < 0 or box[2] > frame.width or box[3] > frame.height:
            return None
        return box

    def read(name, **options):
        box = region(name)
        return ocr_box(frame, box, psm=7, **options) if box is not None else ""

    if not all("combat power change" in read(name).lower()
               for name in ("left_cp_label", "right_cp_label")):
        raise ValueError("已定位比较面板，但未能确认两侧 Combat Power Change 标题")
    if found_position and panel is not None:
        panel["position"] = (frame.size, position, panel_pixels(frame, *position))
    templates = potential_templates()
    names = tuple(f"after_{index}" for index in range(1, 4))
    raw_lines = [""] * 3
    known_lines = [None] * 3
    unknown = (frozenset(POTENTIAL_TYPES), "未读取（已确定未命中目标）")

    def settled_lines():
        lines = tuple(line if line is not None else unknown for line in known_lines)
        if all(line is not None for line in known_lines):
            return lines
        if targets is not None and matches_potential(targets, lines) is False:
            return lines
        return None

    for index, name in enumerate(names):
        box = region(name)
        text = templates.get(potential_fingerprint(frame.crop(box))) if box and templates else None
        if text:
            raw_lines[index] = text
            parsed = parse_potential(text, expected_count=1)
            if parsed:
                known_lines[index] = parsed[0]
    lines = settled_lines()
    if lines is None:
        for index, name in enumerate(names):
            if known_lines[index] is not None:
                continue
            raw_lines[index] = read(name, threshold=150).strip()
            parsed = parse_potential(raw_lines[index], expected_count=1)
            if parsed:
                known_lines[index] = parsed[0]
            lines = settled_lines()
            if lines is not None:
                break
    if lines is None:
        raw = " | ".join(line or "（空）" for line in raw_lines)
        raise ValueError(f"无法确认三条 AFTER 词条。OCR 原文：{raw}")
    values = []
    for name in ("left", "right") if read_cp else ():
        try:
            value = parse_number(read(name, numeric=True, threshold=150), allow_spacing=False)
        except ValueError:
            try:
                value = parse_number(read(name, numeric=True), allow_spacing=False)
            except ValueError:
                # At lower contrast Tesseract can split a digit group with spaces.
                text = read(name, numeric=True, threshold=90)
                try:
                    value = parse_number(text)
                except ValueError as error:
                    side = "左侧" if name == "left" else "右侧"
                    raise ValueError(f"{side} CP 数值无法确认。OCR 原文：{text.strip() or '（空）'}") from error
        values.append(value)
    if not re.search(r"\breset\s*x\s*1\b", read("reset").lower()):
        raise ValueError("已读取 CP 和词条，但未能确认 Reset x1")
    return tuple(values) if read_cp else (None, None), lines


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


CONFIRMATION_PATCHES = {
    "confirm1": {"message": (1087, 638, 1474, 706), "buttons": (1107, 766, 1453, 824)},
    "confirm2": {"message": (1192, 687, 1369, 719), "crystal": (1240, 634, 1398, 662),
                 "buttons": (1107, 800, 1453, 858)},
}


@lru_cache(maxsize=5)
def confirmation_template(name):
    try:
        with Image.open(Path(__file__).with_name("confirmation-templates") / f"{name}.png") as image:
            return image.convert("RGB")
    except OSError:
        return None


def matches_confirmation_template(frame, expected):
    for name, bounds in CONFIRMATION_PATCHES[expected].items():
        template = confirmation_template(f"{expected}-{name}")
        if template is None:
            return False
        left, top, right, bottom = bounds
        box = (round(left * frame.width / 2560), round(top * frame.height / 1440),
               round(right * frame.width / 2560), round(bottom * frame.height / 1440))
        patch = frame.crop(box).convert("RGB")
        if patch.size != template.size:
            patch = patch.resize(template.size, Image.Resampling.BILINEAR)
        difference = ImageChops.difference(template, patch)
        if max(ImageStat.Stat(difference).rms) > 12:
            return False
    return True


def read_confirmation(frame, expected):
    # The two dialogs share a center, but the material dialog is taller.
    def read(box, psm):
        left, top, right, bottom = box
        scaled = (round(left * frame.width / 2048), round(top * frame.height / 1152),
                  round(right * frame.width / 2048), round(bottom * frame.height / 1152))
        return ocr_box(frame, scaled, psm=psm).strip()

    message = read((810, 471, 1240, 581), 6)
    top = 621 if expected == "confirm1" else 648
    confirm = read((900, top, 1002, top + 29), 7)
    cancel = read((1043, top, 1148, top + 29), 7)
    return "\n".join((message, confirm, cancel))


def numbers(frame):
    values = []
    for name in ("left", "right"):
        first = parse_number(ocr(frame, name, numeric=True))
        second = parse_number(ocr(frame, name, numeric=True, threshold=150))
        if first != second:
            raise ValueError(name)
        values.append(first)
    return tuple(values)


class MouseInput(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG), ("data", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class KeyboardInput(ctypes.Structure):
    _fields_ = [("vk", wintypes.WORD), ("scan", wintypes.WORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class InputData(ctypes.Union):
    _fields_ = [("mouse", MouseInput), ("keyboard", KeyboardInput)]


class Input(ctypes.Structure):
    _fields_ = [("kind", wintypes.DWORD), ("data", InputData)]


class BitmapHeader(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
        ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
        ("image_size", wintypes.DWORD), ("x_pixels_per_meter", wintypes.LONG),
        ("y_pixels_per_meter", wintypes.LONG), ("colors_used", wintypes.DWORD),
        ("important_colors", wintypes.DWORD),
    ]


class RegionCapture:
    def __init__(self, user):
        self.user = user
        self.gdi = gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        handle, integer = wintypes.HANDLE, ctypes.c_int
        user.GetDC.argtypes, user.GetDC.restype = (handle,), handle
        user.ReleaseDC.argtypes = (handle, handle)
        gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = (handle,), handle
        gdi.CreateDIBSection.argtypes = (
            handle, ctypes.POINTER(BitmapHeader), wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), handle, wintypes.DWORD,
        )
        gdi.CreateDIBSection.restype = handle
        gdi.SelectObject.argtypes, gdi.SelectObject.restype = (handle, handle), handle
        gdi.BitBlt.argtypes = (
            handle, integer, integer, integer, integer, handle, integer, integer, wintypes.DWORD,
        )
        gdi.GdiFlush.argtypes = ()
        gdi.DeleteDC.argtypes = (handle,)
        gdi.DeleteObject.argtypes = (handle,)

    def grab(self, x, y, width, height):
        screen = self.user.GetDC(None)
        if not screen:
            raise OSError("GetDC failed")
        target = bitmap = previous = None
        try:
            target = self.gdi.CreateCompatibleDC(screen)
            if not target:
                raise OSError("CreateCompatibleDC failed")
            header = BitmapHeader(ctypes.sizeof(BitmapHeader), width, -height, 1, 32)
            pixels = ctypes.c_void_p()
            bitmap = self.gdi.CreateDIBSection(screen, ctypes.byref(header), 0, ctypes.byref(pixels), None, 0)
            if not bitmap or not pixels.value:
                raise OSError("CreateDIBSection failed")
            previous = self.gdi.SelectObject(target, bitmap)
            if previous in (None, ctypes.c_void_p(-1).value):
                previous = None
                raise OSError("SelectObject failed")
            if not self.gdi.BitBlt(target, 0, 0, width, height, screen, x, y, 0x00CC0020):
                raise OSError("BitBlt failed")
            self.gdi.GdiFlush()
            return Image.frombytes("RGB", (width, height), ctypes.string_at(pixels, width * height * 4),
                                   "raw", "BGRX")
        finally:
            if previous:
                self.gdi.SelectObject(target, previous)
            if bitmap:
                self.gdi.DeleteObject(bitmap)
            if target:
                self.gdi.DeleteDC(target)
            self.user.ReleaseDC(None, screen)


class Window:
    def __init__(self, *, detailed_log=True):
        user = ctypes.WinDLL("user32", use_last_error=True)
        try:
            user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            user.SetProcessDPIAware()
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        user.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
        user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        user.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
        user.MapVirtualKeyW.restype = wintypes.UINT
        user.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
        user.SendInput.restype = wintypes.UINT
        self.user = user
        self.capture = RegionCapture(user)
        self.handle = user.GetForegroundWindow()
        title = ctypes.create_unicode_buffer(512)
        user.GetWindowTextW(self.handle, title, len(title))
        if not self.handle or not title.value:
            raise RuntimeError("没有读到前台窗口标题")
        if detailed_log:
            print(f"已锁定窗口 {title.value}", flush=True)

    def check(self):
        if stop_requested.is_set():
            raise RuntimeError("已手动停止")
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
        try:
            return self.capture.grab(origin.x, origin.y, rect.right, rect.bottom)
        except OSError:
            return ImageGrab.grab((origin.x, origin.y, origin.x + rect.right,
                                   origin.y + rect.bottom), all_screens=True)

    def key(self, vk):
        self.check()
        name = "空格" if vk == 0x20 else "回车"
        scan = self.user.MapVirtualKeyW(vk, 0)
        if not scan:
            raise RuntimeError(f"无法获取{name}的扫描码")

        def send(flags, action):
            event = Input(1, InputData(keyboard=KeyboardInput(0, scan, flags, 0, 0)))
            ctypes.set_last_error(0)
            if self.user.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
                code = ctypes.get_last_error()
                detail = f"（Windows 错误 {code}）" if code else ""
                raise RuntimeError(f"Windows 未接受{name}{action}事件{detail}")

        send(0x0008, "按下")
        try:
            time.sleep(0.06)
        finally:
            send(0x0008 | 0x0002, "松开")
        self.pause(KEY_INTERVAL + random.uniform(-KEY_JITTER, KEY_JITTER))


def wait_for(window, expected, *, allow_failure=True, read_cp=True, targets=None, panel=None):
    started = time.monotonic()
    deadline = started + TIMEOUT
    previous = None
    comparison_error = None
    while time.monotonic() < deadline:
        frame = window.screenshot()
        modal = has_teal_dialog(frame)
        state = None
        if modal and expected in ("confirm1", "confirm2"):
            if matches_confirmation_template(frame, expected):
                state = expected
            else:
                cropped = read_confirmation(frame, expected)
                state = classify(cropped, "", True)
                comparison_error = "确认框 OCR 原文：" + " | ".join(cropped.splitlines())
        if state is None and (modal or expected != RESULT):
            state = classify(ocr(frame, "dialog"), "", modal)
        observed = None
        if state == FAILURE and allow_failure:
            observed = (state, None)
        elif state == expected and state != RESULT:
            observed = (state, None)
        if (expected == RESULT and state is None and not modal and panel
                and time.monotonic() - started < 0.6
                and cached_comparison_position(frame, panel) is None):
            previous = None
            window.pause(0.12)
            continue
        if observed is None and expected == RESULT and state is None and not modal:
            try:
                observed = (RESULT, comparison_reading(
                    frame, read_cp=read_cp, targets=targets, panel=panel
                ))
            except ValueError as error:
                comparison_error = str(error)
        if observed is None and expected == RESULT and state is None and not modal:
            if classify(ocr(frame, "dialog"), ocr(frame, "reset")) == RESULT:
                try:
                    values = numbers(frame) if read_cp else (None, None)
                    lines = parse_potential(ocr(frame, "after_lines", psm=6)) if targets is not None else ()
                    if lines or targets is None or (read_cp and values[1] > values[0]):
                        observed = (RESULT, (values, lines))
                except ValueError:
                    pass
        if observed is not None and observed == previous:
            return observed
        if observed is not None:
            comparison_error = "连续两次读取尚未一致"
        previous = observed
        window.pause(0.12 if observed is not None else 0.25)
    stage = {RESULT: "结果页", "confirm1": "首次重洗确认框", "confirm2": "材料确认框"}[expected]
    detail = comparison_error or "未能确认该界面"
    raise RuntimeError(f"等待{stage}超时\n原因：{detail}")


def play_stop_sound(frequencies):
    import winsound

    try:
        for frequency in frequencies:
            winsound.Beep(frequency, 160)
    except RuntimeError:
        pass


def run(window, targets=None, stop_on_cp=True, *, detailed_log=True):
    reading_options = {
        "read_cp": stop_on_cp,
        "targets": targets,
        "panel": {},
    }
    _, (values, lines) = wait_for(window, RESULT, allow_failure=False, **reading_options)
    while True:
        left, right = values
        if detailed_log and left is not None:
            print(f"左 CP {left:,} | 右 CP {right:,}", flush=True)
        if detailed_log and lines:
            print("AFTER 词条  " + " | ".join(line for _, line in lines), flush=True)
        match = matches_potential(targets, lines) if targets is not None else False
        if match:
            play_stop_sound((660, 880))
            return "AFTER 词条符合条件，请在游戏中选择 AFTER"
        if stop_on_cp and right > left:
            play_stop_sound((660, 880))
            return "右侧 CP 更高，请在游戏中选择 AFTER"
        if match is None:
            return "AFTER 有截断词条可能命中目标，请在游戏中确认"
        for expected in ("confirm1", "confirm2", RESULT):
            window.key(0x20)
            state, reading = wait_for(window, expected, **reading_options)
            if state == FAILURE:
                if detailed_log:
                    print("服务器提示稍后重试，正在关闭提示框", flush=True)
                window.pause(KEY_INTERVAL + random.uniform(-KEY_JITTER, KEY_JITTER))
                window.key(0x0D)
                _, (values, lines) = wait_for(window, RESULT, allow_failure=False, **reading_options)
                break
            if state == RESULT:
                values, lines = reading


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--potential", action="store_true")
    parser.add_argument("--also-cp", action="store_true")
    parser.add_argument("--target", nargs=3, metavar=("FIRST", "SECOND", "THIRD"))
    parser.add_argument("--switch-now", action="store_true")
    parser.add_argument("--brief", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    detailed_log = not args.brief
    if args.target is not None:
        try:
            targets = [parse_target(value) for value in args.target]
        except ValueError as error:
            parser.error(str(error))
    else:
        targets = None
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
    if targets is None and args.potential:
        targets = configure_targets()
    if args.switch_now:
        def listen_for_stop():
            try:
                os.read(sys.stdin.fileno(), 1)
            finally:
                stop_requested.set()

        threading.Thread(target=listen_for_stop, daemon=True).start()
        if detailed_log:
            print("请在 5 秒内切回游戏的魔方结果页。", flush=True)
        for seconds in range(5, 0, -1):
            print(f"切回游戏倒计时 {seconds}", flush=True)
            if stop_requested.wait(1):
                print("已手动停止。", flush=True)
                return
    else:
        input("请先打开魔方结果页。按回车后有 5 秒切回游戏窗口……")
        time.sleep(5)
    try:
        print(run(Window(detailed_log=detailed_log), targets, targets is None or args.also_cp,
                  detailed_log=detailed_log))
    except RuntimeError as error:
        message = str(error)
        if not detailed_log:
            message = message.partition("\n")[0]
        print(f"已停止。{message}")


if __name__ == "__main__":
    main()
