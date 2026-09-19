import os
import sys
import hashlib
import threading
import queue
import subprocess
import time
import stat
import shutil
import ctypes
import tempfile
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
from collections import defaultdict, OrderedDict
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from PIL import Image, ImageTk, ImageOps, ImageStat, ImageFilter
    PIL_AVAILABLE = True
except Exception:
    Image = ImageTk = ImageOps = ImageStat = ImageFilter = None
    PIL_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except Exception:
    cv2 = None
    CV2_AVAILABLE = False

try:
    from send2trash import send2trash
    SEND2TRASH_AVAILABLE = True
except Exception:
    send2trash = None
    SEND2TRASH_AVAILABLE = False


APP_TITLE = "NexaClean Pro 3.5 — Duplicate Cleaner + Smart Photo Gallery + Smart Force Delete"
HASH_CHUNK_SIZE = 8 * 1024 * 1024
QUICK_HASH_CHUNK_SIZE = 128 * 1024
PREVIEW_SIZE = (460, 360)
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff",
    ".heic", ".heif", ".avif", ".jfif", ".ico"
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v", ".mpeg", ".mpg"
}
PROTECTED_TOP_LEVEL_DIRS = {
    "windows", "windows.old", "program files", "program files (x86)", "programdata",
    "system volume information", "$recycle.bin", "recovery", "boot", "efi", "perflogs"
}
PROTECTED_ROOT_FILES = {
    "pagefile.sys", "hiberfil.sys", "swapfile.sys", "bootmgr", "bootnxt", "memory.dmp"
}
PREVIEW_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dup-preview")


def is_windows_admin() -> bool:
    if not sys.platform.startswith("win"):
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _same_or_inside(path: str, base: str) -> bool:
    """True when path == base or path is physically below base (case-insensitive on Windows)."""
    if not path or not base:
        return False
    try:
        p = os.path.normcase(os.path.abspath(path)).rstrip("\\/")
        b = os.path.normcase(os.path.abspath(base)).rstrip("\\/")
        return p == b or p.startswith(b + os.sep)
    except Exception:
        return False


def _contains_path(container: str, child: str) -> bool:
    """True when container is the same as child or is one of its ancestors."""
    return _same_or_inside(child, container)


def force_delete_block_reason(path: str) -> str:
    """Hard safety rail for the force-delete tab.

    Old/secondary Windows installations are allowed, but the currently running Windows,
    its live Program Files/ProgramData trees, a drive root, and the whole current user
    profile are never accepted by this tab.
    """
    if not path:
        return "پوشه‌ای انتخاب نشده است."
    try:
        ap = os.path.abspath(path)
    except Exception:
        return "مسیر معتبر نیست."
    if not os.path.isdir(ap):
        return "این مسیر پوشه معتبر و موجود نیست."

    drive, tail = os.path.splitdrive(ap)
    if drive and not tail.strip("\\/"):
        return "حذف اجباری ریشه کامل یک درایو مسدود است."

    if sys.platform.startswith("win"):
        critical = []
        for key in ("WINDIR", "SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            value = os.environ.get(key)
            if value:
                critical.append((key, value))
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            # Exact profile or an ancestor of it is too dangerous. Subfolders are allowed.
            if os.path.normcase(os.path.abspath(ap)).rstrip("\\/") == os.path.normcase(os.path.abspath(user_profile)).rstrip("\\/"):
                return "حذف کامل پروفایل کاربر فعلی مسدود است. یک زیرپوشه مشخص انتخاب کنید."
            if _contains_path(ap, user_profile):
                return "این پوشه شامل پروفایل کاربر فعلی است و حذف اجباری آن مسدود شده است."

        for label, cpath in critical:
            if _same_or_inside(ap, cpath):
                return f"این مسیر داخل بخش فعال سیستم ({label}) است و قابل حذف اجباری نیست."
            if _contains_path(ap, cpath):
                return f"این پوشه شامل بخش فعال سیستم ({label}) است و قابل حذف اجباری نیست."
    return ""


def looks_like_old_windows_folder(path: str) -> bool:
    name = os.path.basename(os.path.abspath(path).rstrip("\\/")).lower()
    if name in {"windows.old", "$windows.~bt", "$windows.~ws"}:
        return True
    if name == "windows" and sys.platform.startswith("win"):
        windir = os.environ.get("WINDIR")
        return bool(windir) and os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(windir))
    return False


def windows_identity() -> str:
    if not sys.platform.startswith("win"):
        return ""
    try:
        result = subprocess.run(
            ["whoami"], capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        ident = (result.stdout or "").strip()
        if ident:
            return ident
    except Exception:
        pass
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return (domain + "\\" + user).strip("\\")


def make_long_windows_path(path: str) -> str:
    """Best-effort long-path form for Python filesystem calls."""
    if not sys.platform.startswith("win"):
        return path
    ap = os.path.abspath(path)
    if ap.startswith("\\\\?\\"):
        return ap
    if ap.startswith("\\\\"):
        return "\\\\?\\UNC\\" + ap.lstrip("\\")
    return "\\\\?\\" + ap


def windows_native_path(path: str) -> str:
    """Return a normal Win32 path for command-line tools such as takeown/icacls.

    GUI/Python paths may contain forward slashes or a Windows long-path prefix.
    Native Windows utilities are more reliable with a regular backslash path.
    """
    if not path:
        return path
    ap = os.path.abspath(path)
    if not sys.platform.startswith("win"):
        return ap
    if ap.startswith("\\\\?\\UNC\\"):
        ap = "\\\\" + ap[8:]
    elif ap.startswith("\\\\?\\"):
        ap = ap[4:]
    return ap.replace("/", "\\")


def schedule_path_delete_on_reboot(path: str) -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        MOVEFILE_DELAY_UNTIL_REBOOT = 0x00000004
        return bool(ctypes.windll.kernel32.MoveFileExW(str(path), None, MOVEFILE_DELAY_UNTIL_REBOOT))
    except Exception:
        return False


def norm_path(path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(path))
    except Exception:
        return os.path.normcase(path or "")


def is_protected_path(path: str) -> bool:
    """Conservative protection for OS/application roots. Used to avoid destructive mistakes."""
    if not path:
        return False
    try:
        ap = os.path.abspath(path)
        if sys.platform.startswith("win"):
            drive, tail = os.path.splitdrive(ap)
            rel = tail.lstrip("\\/")
            parts = [part for part in rel.replace("/", "\\").split("\\") if part]
            if not parts:
                return False
            first = parts[0].lower()
            if first in PROTECTED_TOP_LEVEL_DIRS:
                return True
            if len(parts) == 1 and first in PROTECTED_ROOT_FILES:
                return True
            windir = os.environ.get("WINDIR")
            if windir:
                win = norm_path(windir)
                nap = norm_path(ap)
                if nap == win or nap.startswith(win + os.sep):
                    return True
            return False
        # Basic protection on macOS/Linux when someone scans the filesystem root.
        nap = os.path.abspath(ap)
        protected = ("/System", "/bin", "/sbin", "/usr", "/etc", "/var", "/Library")
        return any(nap == base or nap.startswith(base + os.sep) for base in protected)
    except Exception:
        return False


def detect_storage_type(folder: str) -> str:
    """Best-effort SSD/HDD detection on Windows. Falls back to Unknown without failing scan."""
    if not sys.platform.startswith("win"):
        return "Unknown"
    try:
        drive = os.path.splitdrive(os.path.abspath(folder))[0].rstrip(":\\/")
        if not drive:
            return "Unknown"
        cmd = (
            "$ErrorActionPreference='Stop'; "
            f"$d=(Get-Partition -DriveLetter '{drive}' | Get-Disk); "
            "$p=$d | Get-PhysicalDisk; "
            "if($p.MediaType){$p.MediaType}else{'Unknown'}"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        text = (out.stdout or "").strip().upper()
        if "SSD" in text:
            return "SSD"
        if "HDD" in text:
            return "HDD"
    except Exception:
        pass
    return "Unknown"


def auto_worker_counts(folder: str):
    """Auto-tune workers for the actual storage type instead of blindly oversubscribing I/O."""
    cpu = max(2, os.cpu_count() or 4)
    memory_cap = 48
    try:
        import psutil  # optional
        available_gb = psutil.virtual_memory().available / (1024 ** 3)
        memory_cap = max(4, min(96, int(available_gb * 8)))
    except Exception:
        pass

    storage = detect_storage_type(folder)
    if storage == "HDD":
        # Too many concurrent seeks can make a mechanical disk dramatically slower.
        hash_workers = max(2, min(6, cpu, memory_cap))
        visual_workers = max(1, min(3, cpu // 2 or 1, memory_cap))
    elif storage == "SSD":
        hash_workers = max(8, min(64, cpu * 4, memory_cap))
        visual_workers = max(2, min(16, cpu * 2, memory_cap))
    else:
        hash_workers = max(4, min(32, cpu * 2, memory_cap))
        visual_workers = max(2, min(10, cpu, memory_cap))
    return hash_workers, visual_workers, storage


def quick_file_hash(path: str, size: int) -> str:
    """Fast pre-filter: first/middle/last chunks. Full SHA-256 still confirms matches."""
    digest = hashlib.blake2b(digest_size=16)
    digest.update(str(size).encode("ascii"))
    with open(path, "rb", buffering=0) as f:
        if size <= QUICK_HASH_CHUNK_SIZE * 3:
            while True:
                chunk = f.read(QUICK_HASH_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        else:
            digest.update(f.read(QUICK_HASH_CHUNK_SIZE))
            middle = max(0, (size // 2) - (QUICK_HASH_CHUNK_SIZE // 2))
            f.seek(middle)
            digest.update(f.read(QUICK_HASH_CHUNK_SIZE))
            f.seek(max(0, size - QUICK_HASH_CHUNK_SIZE))
            digest.update(f.read(QUICK_HASH_CHUNK_SIZE))
    return digest.hexdigest()


@dataclass
class DuplicatePair:
    keeper: str
    candidate: str
    kind: str  # exact | visual
    group: int


@dataclass
class ScanResult:
    pairs: list
    total_files: int
    exact_groups: int
    visual_groups: int
    reclaimable_bytes: int
    skipped_protected: int = 0
    skipped_errors: int = 0
    skipped_empty: int = 0
    storage_type: str = "Unknown"


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def file_sha256(path: str, stop_event: threading.Event = None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb", buffering=0) as f:
        while True:
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("scan cancelled")
            chunk = f.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def image_signature(path: str):
    """
    Build a conservative visual signature that survives normal format changes
    (for example PNG -> JPG) without trusting the filename/extension.

    dHash alone has known collisions (especially flat images), so we combine it
    with aspect ratio and a coarse average RGB value.
    """
    if not PIL_AVAILABLE:
        return None
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            width, height = img.size
            if width <= 0 or height <= 0:
                return None

            gray = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(gray.getdata())
            dhash = 0
            for row in range(8):
                offset = row * 9
                for col in range(8):
                    dhash <<= 1
                    if pixels[offset + col] > pixels[offset + col + 1]:
                        dhash |= 1

            tiny = img.resize((8, 8), Image.Resampling.BILINEAR)
            colors = list(tiny.getdata())
            count = len(colors) or 1
            avg = tuple(sum(pixel[channel] for pixel in colors) // count for channel in range(3))
            avg_bucket = tuple(value // 16 for value in avg)
            aspect_bucket = round(width / height, 3)

            return (dhash, aspect_bucket, avg_bucket)
    except Exception:
        return None



# ---------- Smart photo-library metadata / classification ----------
COMMON_SCREEN_DIMS = {
    (1920, 1080), (1366, 768), (1360, 768), (1280, 720), (1600, 900),
    (2560, 1440), (3840, 2160), (1080, 1920), (1080, 2400), (1080, 2340),
    (1080, 2280), (1080, 2160), (1080, 2220), (1080, 2316), (720, 1560), (750, 1560), (1170, 2532), (1179, 2556), (1242, 2688),
    (1290, 2796), (1440, 2560), (1440, 2960), (1440, 3120), (1440, 3200), (720, 1280), (720, 1600),
}
SCREENSHOT_NAME_TOKENS = (
    "screenshot", "screen shot", "screen_shot", "capture", "snipping", "snip_",
    "اسکرین", "screencap"
)
CACHE_PATH_TOKENS = (
    "\\cache\\", "/cache/", "\\caches\\", "/caches/", "\\temp\\", "/temp/",
    "\\tmp\\", "/tmp/", "\\thumbnails\\", "/thumbnails/", "thumbcache",
    "\\inetcache\\", "\\temporary internet files\\"
)
UI_PATH_TOKENS = (
    "\\windows\\", "\\program files\\", "\\program files (x86)\\", "\\appdata\\",
    "\\programdata\\", "\\system32\\", "\\syswow64\\", "\\winsxs\\",
    "\\node_modules\\", "\\site-packages\\", "/applications/", "/library/"
)
PERSONAL_PATH_TOKENS = (
    "\\dcim\\", "/dcim/", "\\camera\\", "/camera/", "\\pictures\\", "/pictures/",
    "\\photos\\", "/photos/"
)

IMAGE_CATEGORY_LABELS = {
    "all": "همه عکس‌ها",
    "screenshot": "اسکرین‌شات / Capture",
    "ui_asset": "آیکون و فایل UI",
    "cache": "Cache / Thumbnail / Temp",
    "quality": "احتمالاً تار / خیلی تیره / روشن",
    "lowres": "رزولوشن پایین",
    "personal": "احتمالاً عکس شخصی / دوربین",
    "other": "سایر تصاویر",
}
RESOLUTION_BUCKETS = ("≤256px / آیکون", "کمتر از 1MP", "1–3MP", "3–8MP", "8–16MP", "16MP+")
FILESIZE_BUCKETS = ("<100KB", "100KB–1MB", "1–5MB", "5–20MB", "20MB+")
ORIENTATION_LABELS = ("افقی", "عمودی", "مربعی")


@dataclass
class ImageRecord:
    path: str
    size: int
    width: int
    height: int
    ext: str
    mtime: float
    category: str
    resolution_bucket: str
    filesize_bucket: str
    orientation: str
    flags: tuple
    has_camera_exif: bool = False
    has_alpha: bool = False
    quality_score: float = 0.0

    @property
    def megapixels(self):
        return (self.width * self.height) / 1_000_000.0


def _path_has_any(path_lower: str, tokens) -> bool:
    normalized = path_lower.replace("/", "\\")
    return any(token.replace("/", "\\") in normalized for token in tokens)


def _resolution_bucket(width: int, height: int) -> str:
    max_dim = max(width, height)
    mp = (width * height) / 1_000_000.0
    if max_dim <= 256:
        return "≤256px / آیکون"
    if mp < 1:
        return "کمتر از 1MP"
    if mp < 3:
        return "1–3MP"
    if mp < 8:
        return "3–8MP"
    if mp < 16:
        return "8–16MP"
    return "16MP+"


def _filesize_bucket(size: int) -> str:
    if size < 100 * 1024:
        return "<100KB"
    if size < 1024 * 1024:
        return "100KB–1MB"
    if size < 5 * 1024 * 1024:
        return "1–5MB"
    if size < 20 * 1024 * 1024:
        return "5–20MB"
    return "20MB+"


def _orientation(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "—"
    ratio = width / height
    if 0.92 <= ratio <= 1.08:
        return "مربعی"
    return "افقی" if width > height else "عمودی"


def inspect_image_record(path: str, size: int, deep_quality: bool = False):
    """Read metadata cheaply, classify likely screenshots/UI assets, optionally estimate quality.

    The classifier is intentionally conservative: it labels and groups; it never deletes by itself.
    """
    if not PIL_AVAILABLE:
        return None
    try:
        with Image.open(path) as img:
            width, height = img.size
            if width <= 0 or height <= 0:
                return None
            ext = Path(path).suffix.lower()
            path_lower = path.lower()
            name_lower = os.path.basename(path).lower()
            try:
                exif = img.getexif()
            except Exception:
                exif = {}
            has_camera_exif = bool(
                exif and (exif.get(271) or exif.get(272) or exif.get(36867) or exif.get(36868))
            )
            try:
                bands = img.getbands()
                has_alpha = "A" in bands or "transparency" in img.info
            except Exception:
                has_alpha = False

            max_dim = max(width, height)
            mp = (width * height) / 1_000_000.0
            exact_screen = (width, height) in COMMON_SCREEN_DIMS or (height, width) in COMMON_SCREEN_DIMS
            screenshot_named = any(token in name_lower for token in SCREENSHOT_NAME_TOKENS)
            screenshot = screenshot_named or (
                exact_screen and not has_camera_exif and ext in {".png", ".jpg", ".jpeg", ".webp"}
            )
            cacheish = _path_has_any(path_lower, CACHE_PATH_TOKENS)
            ui_path = _path_has_any(path_lower, UI_PATH_TOKENS) or is_protected_path(path)
            tiny = max_dim <= 256
            ui_asset = (
                (tiny and (has_alpha or ext in {".png", ".ico", ".bmp", ".gif"}))
                or (ui_path and max_dim <= 1400 and not has_camera_exif)
            )
            lowres = mp < 0.5 or max_dim < 800
            personal_path = _path_has_any(path_lower, PERSONAL_PATH_TOKENS)
            personal = has_camera_exif or (personal_path and not ui_asset and not cacheish and not screenshot)

            flags = []
            if screenshot:
                flags.append("اسکرین‌شات")
            if ui_asset:
                flags.append("UI/آیکون")
            if cacheish:
                flags.append("Cache/Temp")
            if tiny:
                flags.append("خیلی کوچک")
            if lowres:
                flags.append("کم‌رزولوشن")
            if has_camera_exif:
                flags.append("EXIF دوربین")
            if has_alpha:
                flags.append("شفاف")

            quality_score = 0.0
            quality_bad = False
            if deep_quality and max_dim >= 500 and not ui_asset:
                try:
                    sample = ImageOps.exif_transpose(img).convert("L")
                    sample.thumbnail((160, 160), Image.Resampling.BILINEAR)
                    stat_obj = ImageStat.Stat(sample)
                    mean = float(stat_obj.mean[0])
                    # FIND_EDGES stdev is a cheap blur proxy; low edge energy can indicate blur.
                    edges = sample.filter(ImageFilter.FIND_EDGES)
                    edge_std = float(ImageStat.Stat(edges).stddev[0])
                    quality_score = edge_std
                    if mean < 28:
                        flags.append("خیلی تیره")
                        quality_bad = True
                    elif mean > 242:
                        flags.append("خیلی روشن")
                        quality_bad = True
                    if edge_std < 7.0 and not screenshot:
                        flags.append("احتمالاً تار")
                        quality_bad = True
                except Exception:
                    pass

            if cacheish:
                category = "cache"
            elif ui_asset:
                category = "ui_asset"
            elif screenshot:
                category = "screenshot"
            elif quality_bad:
                category = "quality"
            elif lowres:
                category = "lowres"
            elif personal:
                category = "personal"
            else:
                category = "other"

            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0.0

            return ImageRecord(
                path=path,
                size=size,
                width=width,
                height=height,
                ext=ext,
                mtime=mtime,
                category=category,
                resolution_bucket=_resolution_bucket(width, height),
                filesize_bucket=_filesize_bucket(size),
                orientation=_orientation(width, height),
                flags=tuple(flags),
                has_camera_exif=has_camera_exif,
                has_alpha=has_alpha,
                quality_score=quality_score,
            )
    except Exception:
        return None


def make_gallery_thumbnail(path: str, target: tuple):
    """Build a gallery thumbnail without decoding more pixels than necessary.

    JPEG ``draft`` lets Pillow ask the decoder for a reduced-resolution image first.
    This matters a lot for 8–50MP phone photos and keeps the Explorer-like gallery
    responsive on large libraries.  BILINEAR is deliberately used here: thumbnails
    do not need the much more expensive LANCZOS pass used by the large preview.
    """
    if not PIL_AVAILABLE:
        return None
    try:
        with Image.open(path) as img:
            try:
                if (getattr(img, "format", "") or "").upper() in {"JPEG", "MPO"}:
                    img.draft("RGB", (max(target[0] * 2, 256), max(target[1] * 2, 192)))
            except Exception:
                pass
            frame = ImageOps.exif_transpose(img)
            # Keep the whole image visible (Explorer-style "contain") while using a
            # high-quality final downsample. JPEG draft above has already reduced the
            # expensive decode size, so LANCZOS here does not bring back the old lag.
            frame.thumbnail(target, Image.Resampling.LANCZOS)

            canvas = Image.new("RGB", target, "#151a23")
            x = (target[0] - frame.width) // 2
            y = (target[1] - frame.height) // 2
            if frame.mode in ("RGBA", "LA") or "transparency" in getattr(frame, "info", {}):
                rgba = frame.convert("RGBA")
                background = Image.new("RGBA", target, "#151a23")
                background.alpha_composite(rgba, (x, y))
                return background.convert("RGB")
            canvas.paste(frame.convert("RGB"), (x, y))
            return canvas
    except Exception:
        return None


def scan_image_library(folder: str, recursive: bool, safe_mode: bool, deep_quality: bool,
                       event_queue: queue.Queue, stop_event: threading.Event):
    hash_workers, visual_workers, storage_type = auto_worker_counts(folder)
    if storage_type == "HDD":
        workers = max(2, min(4, visual_workers))
    elif storage_type == "SSD":
        workers = max(4, min(16, visual_workers))
    else:
        workers = max(3, min(8, visual_workers))

    counters = defaultdict(int)
    started = time.perf_counter()
    total_seen = 0
    image_seen = 0
    processed = 0
    batch = []
    pending = {}
    max_pending = max(16, workers * 4)
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="photo-meta")

    def flush_batch():
        nonlocal batch
        if batch:
            event_queue.put(("image_batch", batch))
            batch = []

    def drain(block=False):
        nonlocal processed
        if not pending:
            return
        if block:
            done, _ = wait(set(pending), return_when=FIRST_COMPLETED)
        else:
            done = {f for f in list(pending) if f.done()}
        for future in done:
            path = pending.pop(future)
            processed += 1
            try:
                rec = future.result()
            except Exception:
                rec = None
            if rec is not None:
                batch.append(rec)
                if len(batch) >= 64:
                    flush_batch()
            else:
                counters["image_errors"] += 1
        if processed and processed % 250 == 0:
            event_queue.put(("image_progress", (
                image_seen,
                processed,
                f"تحلیل عکس‌ها: {processed:,}/{image_seen:,} • Worker: {workers} • دیسک: {storage_type}"
            )))

    try:
        event_queue.put(("image_status", f"اسکن گالری شروع شد • دیسک: {storage_type} • Worker تصویر: {workers}"))
        for path, size in iter_file_info(folder, recursive, safe_mode, counters):
            if stop_event.is_set():
                flush_batch()
                event_queue.put(("image_cancelled", None))
                return
            total_seen += 1
            if Path(path).suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            image_seen += 1
            while len(pending) >= max_pending and not stop_event.is_set():
                drain(block=True)
            if stop_event.is_set():
                break
            pending[pool.submit(inspect_image_record, path, size, deep_quality)] = path
            if image_seen % 100 == 0:
                drain(block=False)
            if image_seen % 500 == 0:
                event_queue.put(("image_progress", (
                    image_seen,
                    processed,
                    f"پیداشده: {image_seen:,} عکس • تحلیل‌شده: {processed:,} • اسکن پوشه ادامه دارد…"
                )))

        while pending and not stop_event.is_set():
            drain(block=True)
        flush_batch()
        if stop_event.is_set():
            event_queue.put(("image_cancelled", None))
            return
        elapsed = max(0.001, time.perf_counter() - started)
        event_queue.put(("image_done", {
            "total_seen": total_seen,
            "image_seen": image_seen,
            "processed": processed,
            "elapsed": elapsed,
            "storage": storage_type,
            "workers": workers,
            "skipped_protected": counters["protected_dirs"] + counters["protected_files"],
            "errors": counters["errors"] + counters["image_errors"],
        }))
    except Exception as exc:
        flush_batch()
        event_queue.put(("image_error", str(exc)))
    finally:
        pool.shutdown(wait=not stop_event.is_set(), cancel_futures=True)

def iter_file_info(folder: str, recursive: bool, safe_mode: bool, counters: dict):
    """Fast scandir traversal with optional protection for OS/application folders."""
    ignored = {"$RECYCLE.BIN", "System Volume Information", ".Trash", ".Trashes"}
    stack = [folder]

    # If the user explicitly selects a protected root while safe mode is on, do not descend.
    if safe_mode and is_protected_path(folder):
        counters["protected_dirs"] += 1
        return

    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if not recursive:
                                continue
                            if entry.name in ignored:
                                counters["protected_dirs"] += 1
                                continue
                            if safe_mode and is_protected_path(entry.path):
                                counters["protected_dirs"] += 1
                                continue
                            stack.append(entry.path)
                            continue
                        if entry.is_file(follow_symlinks=False):
                            if safe_mode and is_protected_path(entry.path):
                                counters["protected_files"] += 1
                                continue
                            try:
                                size = entry.stat(follow_symlinks=False).st_size
                            except (OSError, PermissionError):
                                counters["errors"] += 1
                                continue
                            yield entry.path, size
                    except (OSError, PermissionError):
                        counters["errors"] += 1
                        continue
        except (OSError, PermissionError):
            counters["errors"] += 1
            continue
        if not recursive:
            break


def choose_keeper(paths):
    """Stable, conservative default: oldest file, then shortest path/name."""
    def key(p):
        try:
            stat = os.stat(p)
            return (stat.st_mtime, len(p), p.lower())
        except OSError:
            return (float("inf"), len(p), p.lower())
    return min(paths, key=key)


def scan_duplicates(folder: str, recursive: bool, find_visual: bool, safe_mode: bool, ignore_empty: bool, event_queue: queue.Queue, stop_event: threading.Event):
    """
    Streaming optimized pipeline:
      1) enumerate metadata with scandir
      2) as soon as a second file with the same size is seen, quick-hash both
      3) full SHA-256 only for quick-hash collisions
      4) emit duplicates immediately while enumeration/hash work is still running
      5) run the optional visual pass only over actual image extensions

    The pending queues are bounded. This lets the app use many workers without
    creating millions of futures or exhausting RAM on very large drives.
    """
    hash_workers, visual_workers, storage_type = auto_worker_counts(folder)
    counters = defaultdict(int)
    indexed_bytes = 0
    image_paths = []
    exact_hash_cache = {}
    exact_pair_keys = set()
    group_counter = 0
    exact_groups = 0
    visual_groups = 0
    reclaimable = 0
    total_files = 0
    candidate_count = 0
    scan_started = time.perf_counter()

    def cancelled():
        return stop_event.is_set()

    def emit_pair(pair: DuplicatePair):
        nonlocal reclaimable
        if cancelled():
            return
        # Results are live. The user may have already deleted either side.
        if not os.path.exists(pair.keeper) or not os.path.exists(pair.candidate):
            return
        try:
            size = os.path.getsize(pair.candidate)
        except OSError:
            size = 0
        reclaimable += size
        event_queue.put(("pair", (pair, size)))

    quick_pool = ThreadPoolExecutor(max_workers=hash_workers, thread_name_prefix="dup-quick")
    full_pool = ThreadPoolExecutor(max_workers=hash_workers, thread_name_prefix="dup-full")
    quick_pending = {}
    full_pending = {}
    full_submitted = set()
    quick_groups = {}
    full_seen = {}
    full_group_ids = {}
    size_first = {}  # size -> first path; becomes None once that size is activated
    quick_processed = 0
    full_processed = 0
    max_quick_pending = max(hash_workers * 4, 16)
    max_full_pending = max(hash_workers * 3, 12)

    def drain_full(block=False):
        nonlocal group_counter, exact_groups, full_processed
        if not full_pending:
            return 0
        if block:
            done, _ = wait(set(full_pending), return_when=FIRST_COMPLETED)
        else:
            done = {f for f in list(full_pending) if f.done()}
        handled = 0
        for future in done:
            path, size, quick_digest = full_pending.pop(future)
            handled += 1
            full_processed += 1
            if cancelled():
                continue
            try:
                digest = future.result()
            except (OSError, PermissionError, InterruptedError):
                continue
            except Exception:
                continue
            exact_hash_cache[path] = digest
            exact_key = (size, quick_digest, digest)
            keeper = full_seen.get(exact_key)
            if keeper is None or not os.path.exists(keeper):
                full_seen[exact_key] = path
                continue
            if keeper == path:
                continue

            pair_key = frozenset((keeper, path))
            if pair_key in exact_pair_keys:
                continue
            exact_pair_keys.add(pair_key)

            group_id = full_group_ids.get(exact_key)
            if group_id is None:
                group_counter += 1
                exact_groups += 1
                group_id = group_counter
                full_group_ids[exact_key] = group_id
            emit_pair(DuplicatePair(keeper, path, "exact", group_id))
        return handled

    def submit_full(path, size, quick_digest):
        if path in full_submitted or cancelled():
            return
        while len(full_pending) >= max_full_pending and not cancelled():
            drain_full(block=True)
        if cancelled():
            return
        full_submitted.add(path)
        future = full_pool.submit(file_sha256, path, stop_event)
        full_pending[future] = (path, size, quick_digest)

    def process_quick_done(done):
        nonlocal quick_processed
        for future in done:
            path, size = quick_pending.pop(future)
            quick_processed += 1
            if cancelled():
                continue
            try:
                quick_digest = future.result()
            except (OSError, PermissionError):
                continue
            except Exception:
                continue

            qkey = (size, quick_digest)
            group = quick_groups.setdefault(qkey, [])
            group.append(path)
            if len(group) == 2:
                submit_full(group[0], size, quick_digest)
                submit_full(group[1], size, quick_digest)
            elif len(group) > 2:
                submit_full(path, size, quick_digest)

        # Surface completed full hashes without waiting for the enumeration to end.
        drain_full(block=False)

    def drain_quick(block=False):
        if not quick_pending:
            return 0
        if block:
            done, _ = wait(set(quick_pending), return_when=FIRST_COMPLETED)
        else:
            done = {f for f in list(quick_pending) if f.done()}
        if done:
            process_quick_done(done)
        return len(done)

    def submit_quick(path, size):
        nonlocal candidate_count
        while len(quick_pending) >= max_quick_pending and not cancelled():
            drain_quick(block=True)
        if cancelled():
            return
        candidate_count += 1
        quick_pending[quick_pool.submit(quick_file_hash, path, size)] = (path, size)

    try:
        event_queue.put(("workers", (hash_workers, visual_workers, storage_type)))
        media_fa = "SSD" if storage_type == "SSD" else ("HDD" if storage_type == "HDD" else "نامشخص")
        event_queue.put(("status", f"اسکن زنده شروع شد • دیسک: {media_fa} • Worker هش: {hash_workers}"))

        # Exact duplicate pipeline starts while the directory tree is still being enumerated.
        for path, size in iter_file_info(folder, recursive, safe_mode, counters):
            if cancelled():
                event_queue.put(("cancelled", None))
                return

            if ignore_empty and size == 0:
                counters["empty"] += 1
                continue

            total_files += 1
            indexed_bytes += size
            if find_visual and PIL_AVAILABLE and Path(path).suffix.lower() in IMAGE_EXTENSIONS:
                image_paths.append(path)

            if size not in size_first:
                size_first[size] = path
            else:
                first = size_first[size]
                if first is not None:
                    submit_quick(first, size)
                    size_first[size] = None
                submit_quick(path, size)

            # Opportunistically process finished work every few discovered files.
            if total_files % 50 == 0:
                drain_quick(block=False)
                drain_full(block=False)

            if total_files % 500 == 0:
                event_queue.put(("progress", (
                    total_files,
                    f"زنده: {total_files:,} فایل ({human_size(indexed_bytes)}) • {quick_processed:,} بررسی سریع • "
                    f"{len(exact_pair_keys):,} تکراری دقیق"
                )))
                event_queue.put(("live_stats", (total_files, exact_groups, visual_groups, reclaimable)))

        # Unique-size paths can now be released.
        size_first.clear()

        while quick_pending:
            if cancelled():
                event_queue.put(("cancelled", None))
                return
            drain_quick(block=True)
            if quick_processed % 100 == 0:
                event_queue.put(("progress", (
                    quick_processed,
                    f"بررسی سریع {quick_processed:,}/{candidate_count:,} • تکراری دقیق: {len(exact_pair_keys):,}"
                )))

        while full_pending:
            if cancelled():
                event_queue.put(("cancelled", None))
                return
            drain_full(block=True)
            if full_processed % 50 == 0:
                event_queue.put(("progress", (
                    full_processed,
                    f"تأیید کامل {full_processed:,} فایل • تکراری دقیق: {len(exact_pair_keys):,}"
                )))

        # Visual pass: only image extensions, parallel and bounded.
        if find_visual and PIL_AVAILABLE and image_paths and not cancelled():
            event_queue.put(("status", f"تشخیص تصویری {len(image_paths):,} عکس با {visual_workers} Worker…"))
            visual_seen = {}  # signature -> {content_key: representative_path}
            visual_group_ids = {}
            visual_processed = 0
            visual_pool = ThreadPoolExecutor(max_workers=visual_workers, thread_name_prefix="dup-visual")
            pending = {}
            path_iter = iter(image_paths)
            max_pending = max(visual_workers * 3, 12)

            def content_key(path):
                digest = exact_hash_cache.get(path)
                if digest is not None:
                    return ("sha256", digest)
                # If two files were byte-identical, the exact pass necessarily gave them
                # the same size + quick hash and cached their full digest.
                return ("path", path)

            def fill_visual():
                while len(pending) < max_pending:
                    try:
                        path = next(path_iter)
                    except StopIteration:
                        break
                    if cancelled():
                        break
                    pending[visual_pool.submit(image_signature, path)] = path

            try:
                fill_visual()
                while pending:
                    if cancelled():
                        event_queue.put(("cancelled", None))
                        return
                    done, _ = wait(set(pending), return_when=FIRST_COMPLETED)
                    for future in done:
                        path = pending.pop(future)
                        visual_processed += 1
                        try:
                            signature = future.result()
                        except Exception:
                            signature = None
                        if signature is None or not os.path.exists(path):
                            continue

                        by_content = visual_seen.setdefault(signature, {})
                        ckey = content_key(path)
                        if ckey in by_content:
                            continue

                        if by_content:
                            keeper = next(iter(by_content.values()))
                            if not os.path.exists(keeper):
                                by_content.clear()
                                by_content[ckey] = path
                                continue
                            pair_key = frozenset((keeper, path))
                            if pair_key not in exact_pair_keys:
                                group_id = visual_group_ids.get(signature)
                                if group_id is None:
                                    group_counter += 1
                                    visual_groups += 1
                                    group_id = group_counter
                                    visual_group_ids[signature] = group_id
                                emit_pair(DuplicatePair(keeper, path, "visual", group_id))
                        by_content[ckey] = path

                        if visual_processed % 50 == 0:
                            event_queue.put(("progress", (
                                visual_processed,
                                f"تصاویر {visual_processed:,}/{len(image_paths):,} • گروه تصویری: {visual_groups:,}"
                            )))
                            event_queue.put(("live_stats", (total_files, exact_groups, visual_groups, reclaimable)))
                    fill_visual()
            finally:
                visual_pool.shutdown(wait=not cancelled(), cancel_futures=True)

        elapsed = max(0.001, time.perf_counter() - scan_started)
        result = ScanResult(
            pairs=[],  # streamed live; keeping a second full copy would waste memory
            total_files=total_files,
            exact_groups=exact_groups,
            visual_groups=visual_groups,
            reclaimable_bytes=reclaimable,
            skipped_protected=counters["protected_dirs"] + counters["protected_files"],
            skipped_errors=counters["errors"],
            skipped_empty=counters["empty"],
            storage_type=storage_type,
        )
        event_queue.put(("done", (result, elapsed, hash_workers, visual_workers)))
    except Exception as exc:
        event_queue.put(("error", str(exc)))
    finally:
        # On cancellation don't wait for queued futures; running SHA jobs observe stop_event.
        quick_pool.shutdown(wait=not cancelled(), cancel_futures=True)
        full_pool.shutdown(wait=not cancelled(), cancel_futures=True)


def open_with_default_app(path: str):
    if not path or not os.path.exists(path):
        return
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        messagebox.showerror("خطا", f"باز کردن فایل ممکن نشد:\n{exc}")


def reveal_in_folder(path: str):
    if not path or not os.path.exists(path):
        return
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception as exc:
        messagebox.showerror("خطا", f"نمایش پوشه ممکن نشد:\n{exc}")


class PreviewCard(ttk.Frame):
    def __init__(self, parent, title):
        super().__init__(parent, style="Card.TFrame", padding=12)
        self.path = None
        self.photo = None
        self.preview_token = 0
        self.preview_queue = queue.Queue()

        header = ttk.Frame(self, style="Card.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text=title, style="CardTitle.TLabel").pack(side="right")

        self.preview = tk.Label(
            self,
            text="فایلی انتخاب نشده",
            bg="#151a23",
            fg="#b7c0ce",
            bd=0,
            width=54,
            height=19,
            font=("Segoe UI", 10),
            compound="center",
        )
        self.preview.pack(fill="both", expand=True, pady=(10, 10))

        self.name_label = ttk.Label(self, text="—", style="FileName.TLabel", anchor="e", justify="right")
        self.name_label.pack(fill="x")
        self.meta_label = ttk.Label(self, text="—", style="Muted.TLabel", anchor="e", justify="right")
        self.meta_label.pack(fill="x", pady=(4, 0))
        self.path_label = ttk.Label(
            self, text="—", style="Muted.TLabel", anchor="e", justify="right", wraplength=520
        )
        self.path_label.pack(fill="x", pady=(4, 0))

        buttons = ttk.Frame(self, style="Card.TFrame")
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="باز کردن", command=self.open_file, style="Ghost.TButton").pack(side="right")
        ttk.Button(buttons, text="نمایش در پوشه", command=self.reveal_file, style="Ghost.TButton").pack(side="right", padx=(0, 8))

        self.after(60, self._poll_preview_queue)

    def clear(self):
        self.preview_token += 1
        self.path = None
        self.photo = None
        self.preview.configure(image="", text="فایلی انتخاب نشده")
        self.name_label.configure(text="—")
        self.meta_label.configure(text="—")
        self.path_label.configure(text="—")

    def set_file(self, path: str):
        self.preview_token += 1
        token = self.preview_token
        self.path = path
        self.photo = None

        if not path or not os.path.exists(path):
            self.preview.configure(image="", text="فایل در دسترس نیست")
            self.name_label.configure(text=os.path.basename(path) if path else "—")
            self.meta_label.configure(text="—")
            self.path_label.configure(text=path or "—")
            return

        try:
            stat = os.stat(path)
            suffix = Path(path).suffix.lower() or "بدون پسوند"
            self.name_label.configure(text=os.path.basename(path))
            self.meta_label.configure(text=f"{human_size(stat.st_size)}   •   {suffix}")
            self.path_label.configure(text=path)
        except OSError:
            self.name_label.configure(text=os.path.basename(path))
            self.meta_label.configure(text="—")
            self.path_label.configure(text=path)

        ext = Path(path).suffix.lower()
        if PIL_AVAILABLE and ext in IMAGE_EXTENSIONS:
            self.preview.configure(image="", text="در حال ساخت پیش‌نمایش…")
            PREVIEW_EXECUTOR.submit(self._load_image_preview, token, path)
            return
        if PIL_AVAILABLE and CV2_AVAILABLE and ext in VIDEO_EXTENSIONS:
            self.preview.configure(image="", text="در حال ساخت پیش‌نمایش ویدیو…")
            PREVIEW_EXECUTOR.submit(self._load_video_preview, token, path)
            return

        ext_text = ext.upper().replace(".", "") or "FILE"
        self.preview.configure(image="", text=f"{ext_text}\n\nپیش‌نمایش داخلی برای این فایل موجود نیست")

    def _fit_pil_image(self, image):
        image = ImageOps.exif_transpose(image)
        image.thumbnail(PREVIEW_SIZE, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", PREVIEW_SIZE, "#151a23")
        x = (PREVIEW_SIZE[0] - image.width) // 2
        y = (PREVIEW_SIZE[1] - image.height) // 2
        if image.mode in ("RGBA", "LA"):
            rgba = image.convert("RGBA")
            canvas.paste(rgba.convert("RGB"), (x, y), rgba.getchannel("A"))
        else:
            canvas.paste(image.convert("RGB"), (x, y))
        return canvas

    def _load_image_preview(self, token: int, path: str):
        frame = None
        try:
            with Image.open(path) as img:
                frame = self._fit_pil_image(img.copy())
        except Exception:
            frame = None
        self.preview_queue.put((token, path, frame, "image"))

    def _load_video_preview(self, token: int, path: str):
        frame_image = None
        try:
            cap = cv2.VideoCapture(path)
            if cap.isOpened():
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if frame_count > 1:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_count // 3))
                ok, frame = cap.read()
                if ok and frame is not None:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    frame_image = self._fit_pil_image(Image.fromarray(frame))
            cap.release()
        except Exception:
            frame_image = None
        self.preview_queue.put((token, path, frame_image, "video"))

    def _poll_preview_queue(self):
        try:
            while True:
                token, path, frame, kind = self.preview_queue.get_nowait()
                if token != self.preview_token or path != self.path:
                    continue
                if frame is None:
                    label = "IMAGE" if kind == "image" else "VIDEO"
                    self.preview.configure(image="", text=f"{label}\n\nپیش‌نمایش قابل ساخت نیست")
                    continue
                try:
                    self.photo = ImageTk.PhotoImage(frame)
                    self.preview.configure(image=self.photo, text="")
                except Exception:
                    self.photo = None
                    self.preview.configure(image="", text="پیش‌نمایش قابل نمایش نیست")
        except queue.Empty:
            pass
        try:
            self.after(60, self._poll_preview_queue)
        except tk.TclError:
            pass

    def open_file(self):
        open_with_default_app(self.path)

    def reveal_file(self):
        reveal_in_folder(self.path)


class DuplicateCleanerApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1460x900")
        self.root.minsize(1100, 720)
        self.root.configure(bg="#0d1117")

        self.queue = queue.Queue()
        self.stop_event = threading.Event()
        self.scan_thread = None
        self.pairs = []
        self.current_index = -1
        self.deleted_count = 0
        self.deleted_bytes = 0
        self.scan_active = False
        self.hash_workers = 0
        self.visual_workers = 0
        self.storage_type = "Unknown"

        # Fast live-result bookkeeping. Avoid walking the entire Treeview on every delete.
        self.path_rows = defaultdict(set)       # normalized path -> row ids
        self.group_rows = defaultdict(set)      # group id -> row ids
        self.group_members = defaultdict(dict)  # group id -> normalized path -> original path
        self.row_sizes = {}
        self.deleted_paths = set()
        self.locked_paths = set()  # only truly protected system paths
        self.delete_problem_paths = {}  # ordinary files that failed to delete; retry is allowed
        self.delete_inflight = set()
        self.bulk_delete_active = False
        self.bulk_delete_total = 0
        self.bulk_delete_processed = 0
        self.bulk_delete_success = 0
        self.bulk_delete_failed = 0
        self.bulk_delete_skipped = 0
        self.visible_count = 0
        self.visible_reclaimable = 0
        self.last_total_files = 0
        self.last_exact_groups = 0
        self.last_visual_groups = 0

        self.folder_var = tk.StringVar(value=os.getcwd())
        self.recursive_var = tk.BooleanVar(value=True)
        # Visual comparison is intentionally off by default because it requires decoding images.
        self.visual_var = tk.BooleanVar(value=False)
        self.safe_mode_var = tk.BooleanVar(value=True)
        self.ignore_empty_var = tk.BooleanVar(value=True)
        self.fast_delete_var = tk.BooleanVar(value=False)
        self.auto_next_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="یک پوشه انتخاب کنید و اسکن را شروع کنید.")
        self.stats_var = tk.StringVar(value="هنوز اسکن نشده")

        # Smart photo gallery state (independent from duplicate results).
        self.image_queue = queue.Queue()
        self.image_stop_event = threading.Event()
        self.image_scan_thread = None
        self.image_scan_active = False
        self.image_records = []
        self.image_filtered = []
        self.image_keep_paths = set()
        self.image_delete_errors = {}
        self.image_delete_active = False
        self.image_page = 0
        self.image_page_size = 72
        self.image_filter_key = "cat:all"
        self.image_render_scheduled = False
        self.image_category_rebuilding = False
        self.image_sidebar_dirty = True
        self.image_gallery_has_snapshot = False
        self.image_render_signature = None
        self.image_thumb_token = 0
        self.image_thumb_cache = OrderedDict()
        self.image_thumb_cache_limit = 320
        thumb_workers = min(8, max(4, (os.cpu_count() or 4)))
        self.image_thumb_executor = ThreadPoolExecutor(max_workers=thumb_workers, thread_name_prefix="gallery-thumb")
        self.image_thumb_widgets = {}
        self.image_thumb_photos = {}

        self.img_deep_quality_var = tk.BooleanVar(value=False)
        self.img_resolution_var = tk.StringVar(value="همه رزولوشن‌ها")
        self.img_filesize_var = tk.StringVar(value="همه حجم‌ها")
        self.img_orientation_var = tk.StringVar(value="همه جهت‌ها")
        self.img_sort_var = tk.StringVar(value="جدیدترین")
        self.img_search_var = tk.StringVar(value="")
        self.img_thumb_size_var = tk.StringVar(value="متوسط")
        self.img_delete_page_only_var = tk.BooleanVar(value=False)
        self.img_status_var = tk.StringVar(value="برای دسته‌بندی عکس‌ها، پوشه را انتخاب و «اسکن عکس‌ها» را بزنید.")
        self.img_stats_var = tk.StringVar(value="هنوز اسکن نشده")

        # Force-folder-delete state. This is intentionally isolated from duplicate/gallery deletion.
        self.force_folder_var = tk.StringVar(value="")
        self.force_status_var = tk.StringVar(value="یک پوشه قدیمی یا قفل‌شده را انتخاب کنید.")
        self.force_admin_var = tk.StringVar(value="")
        self.force_takeown_var = tk.BooleanVar(value=True)
        self.force_clear_attrs_var = tk.BooleanVar(value=True)
        self.force_reboot_var = tk.BooleanVar(value=True)
        self.force_confirm_var = tk.BooleanVar(value=False)
        self.force_queue = queue.Queue()
        self.force_delete_stop_event = threading.Event()
        self.force_delete_thread = None
        self.force_delete_active = False
        self.force_active_proc = None
        self.force_log_lines = []
        self.force_log_file = None
        self.force_last_delete_stats = {}

        self._configure_styles()
        self._build_ui()
        self.root.after(35, self._poll_queue)
        self.root.after(55, self._poll_image_queue)
        self.root.after(80, self._poll_force_queue)

    def _configure_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("App.TFrame", background="#0d1117")
        style.configure("Top.TFrame", background="#111827")
        style.configure("Card.TFrame", background="#111827")
        style.configure("TLabel", background="#0d1117", foreground="#e5e7eb", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#111827", foreground="#ffffff", font=("Segoe UI Semibold", 18))
        style.configure("Subtitle.TLabel", background="#111827", foreground="#9ca3af", font=("Segoe UI", 9))
        style.configure("CardTitle.TLabel", background="#111827", foreground="#f9fafb", font=("Segoe UI Semibold", 11))
        style.configure("FileName.TLabel", background="#111827", foreground="#ffffff", font=("Segoe UI Semibold", 10))
        style.configure("Muted.TLabel", background="#111827", foreground="#9ca3af", font=("Segoe UI", 9))
        style.configure("Status.TLabel", background="#0d1117", foreground="#a7b0be", font=("Segoe UI", 9))

        style.configure("TCheckbutton", background="#0d1117", foreground="#d1d5db", font=("Segoe UI", 9))
        style.map("TCheckbutton", background=[("active", "#0d1117")])

        style.configure("Primary.TButton", padding=(14, 9), font=("Segoe UI Semibold", 9), background="#2563eb", foreground="#ffffff", borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("disabled", "#374151")])
        style.configure("Danger.TButton", padding=(14, 9), font=("Segoe UI Semibold", 9), background="#dc2626", foreground="#ffffff", borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#b91c1c"), ("disabled", "#374151")])
        style.configure("Ghost.TButton", padding=(11, 7), font=("Segoe UI", 9), background="#1f2937", foreground="#e5e7eb", borderwidth=0)
        style.map("Ghost.TButton", background=[("active", "#374151")])

        style.configure("Treeview", background="#111827", fieldbackground="#111827", foreground="#d1d5db", rowheight=34, borderwidth=0, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#1f2937", foreground="#f9fafb", relief="flat", font=("Segoe UI Semibold", 9))
        style.map("Treeview", background=[("selected", "#1d4ed8")], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", "#374151")])
        style.configure("Horizontal.TProgressbar", troughcolor="#1f2937", background="#3b82f6", bordercolor="#1f2937", lightcolor="#3b82f6", darkcolor="#3b82f6")
        style.configure("TNotebook", background="#0d1117", borderwidth=0)
        style.configure("TNotebook.Tab", background="#1f2937", foreground="#d1d5db", padding=(18, 9), font=("Segoe UI Semibold", 9))
        style.map("TNotebook.Tab", background=[("selected", "#2563eb")], foreground=[("selected", "#ffffff")])

    def _build_ui(self):
        # Persistent header; all existing duplicate-cleaner features live in the first tab.
        top = ttk.Frame(self.root, style="Top.TFrame", padding=(22, 16))
        top.pack(fill="x")
        title_wrap = ttk.Frame(top, style="Top.TFrame")
        title_wrap.pack(side="right")
        ttk.Label(title_wrap, text="NexaClean Pro — نکسا کلین پرو", style="Title.TLabel").pack(anchor="e")
        ttk.Label(
            title_wrap,
            text="اسکن زنده + حذف گروهی + گالری هوشمند + حذف اجباری پوشه‌های قدیمی ویندوز",
            style="Subtitle.TLabel",
        ).pack(anchor="e", pady=(3, 0))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(8, 8))
        dup_tab = ttk.Frame(self.notebook, style="App.TFrame")
        img_tab = ttk.Frame(self.notebook, style="App.TFrame")
        force_tab = ttk.Frame(self.notebook, style="App.TFrame")
        self.notebook.add(dup_tab, text="فایل‌های تکراری")
        self.notebook.add(img_tab, text="مدیریت و پاکسازی عکس‌ها")
        self.notebook.add(force_tab, text="حذف اجباری پوشه")

        # ---------------- Existing duplicate cleaner tab ----------------
        folder_row = ttk.Frame(dup_tab, style="App.TFrame", padding=(20, 14, 20, 8))
        folder_row.pack(fill="x")
        self.scan_btn = ttk.Button(folder_row, text="شروع اسکن", command=self.start_scan, style="Primary.TButton")
        self.scan_btn.pack(side="left")
        self.cancel_btn = ttk.Button(folder_row, text="لغو", command=self.cancel_scan, style="Ghost.TButton", state="disabled")
        self.cancel_btn.pack(side="left", padx=(8, 0))
        ttk.Button(folder_row, text="انتخاب پوشه", command=self.choose_folder, style="Ghost.TButton").pack(side="right")
        self.folder_entry = ttk.Entry(folder_row, textvariable=self.folder_var, justify="right")
        self.folder_entry.pack(side="right", fill="x", expand=True, padx=(0, 10), ipady=6)

        options = ttk.Frame(dup_tab, style="App.TFrame", padding=(20, 0, 20, 8))
        options.pack(fill="x")
        ttk.Checkbutton(options, text="زیرپوشه‌ها", variable=self.recursive_var).pack(side="right")
        ttk.Checkbutton(options, text="حالت امن: رد کردن Windows / Program Files", variable=self.safe_mode_var).pack(side="right", padx=(0, 14))
        ttk.Checkbutton(options, text="نادیده‌گرفتن فایل خالی", variable=self.ignore_empty_var).pack(side="right", padx=(0, 14))
        visual_cb = ttk.Checkbutton(options, text="تشخیص عکس مشابه (کندتر)", variable=self.visual_var)
        visual_cb.pack(side="right", padx=(0, 14))
        if not PIL_AVAILABLE:
            visual_cb.configure(state="disabled")
            self.visual_var.set(False)

        delete_options = ttk.Frame(dup_tab, style="App.TFrame", padding=(20, 0, 20, 10))
        delete_options.pack(fill="x")
        self.fast_delete_cb = ttk.Checkbutton(delete_options, text="حذف سریع بدون سؤال (فقط سطل زباله)", variable=self.fast_delete_var)
        self.fast_delete_cb.pack(side="right")
        if not SEND2TRASH_AVAILABLE:
            self.fast_delete_cb.configure(state="disabled")
            self.fast_delete_var.set(False)
        ttk.Checkbutton(delete_options, text="بعد از حذف خودکار برو مورد بعدی", variable=self.auto_next_var).pack(side="right", padx=(0, 16))
        ttk.Label(delete_options, textvariable=self.stats_var, style="Status.TLabel").pack(side="left")

        progress_wrap = ttk.Frame(dup_tab, style="App.TFrame", padding=(20, 0, 20, 8))
        progress_wrap.pack(fill="x")
        self.progress = ttk.Progressbar(progress_wrap, mode="indeterminate")
        self.progress.pack(fill="x")
        ttk.Label(progress_wrap, textvariable=self.status_var, style="Status.TLabel", anchor="e").pack(fill="x", pady=(6, 0))

        body = ttk.Frame(dup_tab, style="App.TFrame", padding=(20, 8, 20, 8))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=0, minsize=390)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        list_card = ttk.Frame(body, style="Card.TFrame", padding=10)
        list_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        ttk.Label(list_card, text="موارد پیدا شده — Delete = حذف", style="CardTitle.TLabel").pack(anchor="e", pady=(0, 8))
        tree_wrap = ttk.Frame(list_card, style="Card.TFrame")
        tree_wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_wrap, columns=("type", "size", "state"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="فایل")
        self.tree.heading("type", text="نوع")
        self.tree.heading("size", text="حجم")
        self.tree.heading("state", text="وضعیت")
        self.tree.column("#0", width=175, minwidth=120)
        self.tree.column("type", width=58, anchor="center")
        self.tree.column("size", width=78, anchor="center")
        self.tree.column("state", width=90, anchor="center")
        scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.tag_configure("locked", foreground="#f59e0b")
        self.tree.tag_configure("warning", foreground="#fbbf24")
        self.tree.tag_configure("deleting", foreground="#60a5fa")
        self.tree.bind("<<TreeviewSelect>>", self.on_select_pair)
        self.tree.bind("<Delete>", lambda e: (self.trash_candidate(), "break")[1])
        self.tree.bind("<Control-Right>", lambda e: (self.next_pair(), "break")[1])
        self.tree.bind("<Control-s>", lambda e: (self.swap_pair(), "break")[1])

        preview_area = ttk.Frame(body, style="App.TFrame")
        preview_area.grid(row=0, column=1, sticky="nsew")
        preview_area.columnconfigure(0, weight=1)
        preview_area.columnconfigure(1, weight=1)
        preview_area.rowconfigure(0, weight=1)
        self.keep_card = PreviewCard(preview_area, "نسخه‌ای که نگه داشته می‌شود")
        self.keep_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.candidate_card = PreviewCard(preview_area, "نسخه پیشنهادی برای حذف")
        self.candidate_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        action_bar = ttk.Frame(dup_tab, style="Top.TFrame", padding=(20, 13))
        action_bar.pack(fill="x")
        self.bulk_trash_btn = ttk.Button(
            action_bar, text="حذف همه تکراری‌های لیست", command=self.trash_all_visible,
            style="Danger.TButton", state="disabled"
        )
        self.bulk_trash_btn.pack(side="left")
        self.trash_btn = ttk.Button(
            action_bar, text="حذف همین مورد", command=self.trash_candidate,
            style="Ghost.TButton", state="disabled"
        )
        self.trash_btn.pack(side="left", padx=(8, 0))
        self.next_btn = ttk.Button(action_bar, text="رد کردن / بعدی", command=self.next_pair, style="Ghost.TButton", state="disabled")
        self.next_btn.pack(side="left", padx=(8, 0))
        self.swap_btn = ttk.Button(action_bar, text="جابه‌جایی دو طرف", command=self.swap_pair, style="Ghost.TButton", state="disabled")
        self.swap_btn.pack(side="left", padx=(8, 0))
        note = "حذف گروهی در پس‌زمینه انجام می‌شود؛ فایل‌های سیستمی مسدود هستند" if SEND2TRASH_AVAILABLE else "send2trash نصب نیست؛ حذف گروهی دائمی است"
        ttk.Label(action_bar, text=note, style="Subtitle.TLabel").pack(side="right")

        # ---------------- Smart photo gallery tab ----------------
        img_folder = ttk.Frame(img_tab, style="App.TFrame", padding=(20, 14, 20, 8))
        img_folder.pack(fill="x")
        self.img_scan_btn = ttk.Button(img_folder, text="اسکن عکس‌ها", command=self.start_image_scan, style="Primary.TButton")
        self.img_scan_btn.pack(side="left")
        self.img_cancel_btn = ttk.Button(img_folder, text="لغو", command=self.cancel_image_scan, style="Ghost.TButton", state="disabled")
        self.img_cancel_btn.pack(side="left", padx=(8, 0))
        ttk.Button(img_folder, text="انتخاب پوشه", command=self.choose_folder, style="Ghost.TButton").pack(side="right")
        ttk.Entry(img_folder, textvariable=self.folder_var, justify="right").pack(side="right", fill="x", expand=True, padx=(0, 10), ipady=6)

        img_opts = ttk.Frame(img_tab, style="App.TFrame", padding=(20, 0, 20, 8))
        img_opts.pack(fill="x")
        ttk.Checkbutton(img_opts, text="زیرپوشه‌ها", variable=self.recursive_var).pack(side="right")
        ttk.Checkbutton(img_opts, text="حالت امن", variable=self.safe_mode_var).pack(side="right", padx=(0, 14))
        ttk.Checkbutton(
            img_opts, text="آنالیز کیفیت (تار/تیره) — کندتر", variable=self.img_deep_quality_var
        ).pack(side="right", padx=(0, 14))
        ttk.Label(img_opts, textvariable=self.img_stats_var, style="Status.TLabel").pack(side="left")

        self.img_progress = ttk.Progressbar(img_tab, mode="indeterminate")
        self.img_progress.pack(fill="x", padx=20)
        ttk.Label(img_tab, textvariable=self.img_status_var, style="Status.TLabel", anchor="e").pack(fill="x", padx=20, pady=(5, 8))

        gallery_body = ttk.Frame(img_tab, style="App.TFrame", padding=(20, 4, 20, 8))
        gallery_body.pack(fill="both", expand=True)
        gallery_body.columnconfigure(0, weight=0, minsize=255)
        gallery_body.columnconfigure(1, weight=1)
        gallery_body.rowconfigure(0, weight=1)

        side = ttk.Frame(gallery_body, style="Card.TFrame", padding=10)
        side.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ttk.Label(side, text="دسته‌بندی", style="CardTitle.TLabel").pack(anchor="e", pady=(0, 8))
        self.img_category_tree = ttk.Treeview(side, show="tree", selectmode="browse", height=20)
        self.img_category_tree.pack(fill="both", expand=True)
        self.img_category_tree.bind("<<TreeviewSelect>>", self._on_image_category_select)
        ttk.Label(
            side,
            text="تیک «نگه دار» یعنی در حذف گروهی آن عکس دست‌نخورده می‌ماند.",
            style="Muted.TLabel", wraplength=225, justify="right"
        ).pack(fill="x", pady=(8, 0))

        main = ttk.Frame(gallery_body, style="App.TFrame")
        main.grid(row=0, column=1, sticky="nsew")
        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)

        filters = ttk.Frame(main, style="Card.TFrame", padding=8)
        filters.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(filters, text="جستجو:", style="Muted.TLabel").pack(side="right")
        search = ttk.Entry(filters, textvariable=self.img_search_var, justify="right", width=20)
        search.pack(side="right", padx=(4, 10), ipady=3)
        search.bind("<KeyRelease>", lambda e: self._schedule_image_filter_refresh())
        ttk.Button(
            filters, text="نمایش نتایج جدید", command=self._manual_refresh_image_gallery,
            style="Ghost.TButton"
        ).pack(side="right", padx=(0, 8))

        for var, values, width in (
            (self.img_resolution_var, ["همه رزولوشن‌ها", *RESOLUTION_BUCKETS], 16),
            (self.img_filesize_var, ["همه حجم‌ها", *FILESIZE_BUCKETS], 13),
            (self.img_orientation_var, ["همه جهت‌ها", *ORIENTATION_LABELS], 11),
            (self.img_sort_var, ["جدیدترین", "قدیمی‌ترین", "بزرگ‌ترین فایل", "بیشترین رزولوشن", "نام"], 15),
            (self.img_thumb_size_var, ["کوچک", "متوسط", "بزرگ"], 9),
        ):
            cb = ttk.Combobox(filters, textvariable=var, values=values, state="readonly", width=width)
            cb.pack(side="right", padx=(0, 7))
            cb.bind("<<ComboboxSelected>>", lambda e: self._schedule_image_filter_refresh(force_page_reset=True))

        gallery_wrap = ttk.Frame(main, style="Card.TFrame")
        gallery_wrap.grid(row=1, column=0, sticky="nsew")
        gallery_wrap.rowconfigure(0, weight=1)
        gallery_wrap.columnconfigure(0, weight=1)
        self.img_canvas = tk.Canvas(gallery_wrap, bg="#0f141d", bd=0, highlightthickness=0)
        img_scroll = ttk.Scrollbar(gallery_wrap, orient="vertical", command=self.img_canvas.yview)
        self.img_canvas.configure(yscrollcommand=img_scroll.set)
        self.img_canvas.grid(row=0, column=0, sticky="nsew")
        img_scroll.grid(row=0, column=1, sticky="ns")
        self.img_grid_frame = ttk.Frame(self.img_canvas, style="App.TFrame")
        self.img_canvas_window = self.img_canvas.create_window((0, 0), window=self.img_grid_frame, anchor="nw")
        self.img_grid_frame.bind("<Configure>", lambda e: self.img_canvas.configure(scrollregion=self.img_canvas.bbox("all")))
        self.img_canvas.bind("<Configure>", self._on_gallery_canvas_resize)

        nav = ttk.Frame(main, style="Top.TFrame", padding=(10, 8))
        nav.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.img_delete_btn = ttk.Button(
            nav, text="حذف عکس‌های فیلترشده به‌جز تیک‌خورده‌ها",
            command=self.delete_filtered_images, style="Danger.TButton", state="disabled"
        )
        self.img_delete_btn.pack(side="left")
        ttk.Checkbutton(nav, text="فقط همین صفحه", variable=self.img_delete_page_only_var).pack(side="left", padx=(10, 0))
        ttk.Button(nav, text="همه را نگه دار", command=self.keep_all_filtered, style="Ghost.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(nav, text="برداشتن تیک‌ها", command=self.clear_keep_filtered, style="Ghost.TButton").pack(side="left", padx=(8, 0))
        self.img_next_btn = ttk.Button(nav, text="بعدی", command=lambda: self.change_image_page(1), style="Ghost.TButton", state="disabled")
        self.img_next_btn.pack(side="right")
        self.img_page_label = ttk.Label(nav, text="صفحه 0/0", style="Subtitle.TLabel")
        self.img_page_label.pack(side="right", padx=10)
        self.img_prev_btn = ttk.Button(nav, text="قبلی", command=lambda: self.change_image_page(-1), style="Ghost.TButton", state="disabled")
        self.img_prev_btn.pack(side="right")

        # ---------------- Force folder delete tab ----------------
        force_top = ttk.Frame(force_tab, style="Top.TFrame", padding=(20, 16))
        force_top.pack(fill="x", padx=10, pady=(10, 0))
        ttk.Label(force_top, text="حذف اجباری پوشه‌های قدیمی / قفل‌شده", style="CardTitle.TLabel").pack(anchor="e")
        ttk.Label(
            force_top,
            text="برای Windows.old، نصب قدیمی ویندوز روی درایو دیگر و پوشه‌هایی که Access Denied می‌دهند. حذف این تب دائمی است و وارد Recycle Bin نمی‌شود.",
            style="Subtitle.TLabel", justify="right"
        ).pack(anchor="e", pady=(5, 0))

        force_folder = ttk.Frame(force_tab, style="App.TFrame", padding=(20, 14, 20, 8))
        force_folder.pack(fill="x")
        ttk.Button(force_folder, text="انتخاب پوشه", command=self.choose_force_folder, style="Ghost.TButton").pack(side="right")
        self.force_folder_entry = ttk.Entry(force_folder, textvariable=self.force_folder_var, justify="right")
        self.force_folder_entry.pack(side="right", fill="x", expand=True, padx=(0, 10), ipady=7)
        ttk.Button(force_folder, text="بررسی مسیر", command=self.inspect_force_folder, style="Primary.TButton").pack(side="left")

        force_admin = ttk.Frame(force_tab, style="Card.TFrame", padding=12)
        force_admin.pack(fill="x", padx=20, pady=(2, 8))
        self.force_admin_label = ttk.Label(force_admin, textvariable=self.force_admin_var, style="Status.TLabel")
        self.force_admin_label.pack(side="right")
        if sys.platform.startswith("win"):
            ttk.Button(force_admin, text="اجرای برنامه با Administrator", command=self.relaunch_as_admin, style="Ghost.TButton").pack(side="left")
        else:
            ttk.Label(force_admin, text="این بخش برای Windows طراحی شده است.", style="Muted.TLabel").pack(side="left")

        force_opts = ttk.Frame(force_tab, style="App.TFrame", padding=(20, 4, 20, 8))
        force_opts.pack(fill="x")
        ttk.Checkbutton(force_opts, text="گرفتن مالکیت پوشه (takeown)", variable=self.force_takeown_var).pack(side="right")
        ttk.Checkbutton(force_opts, text="برداشتن Read-only / System / Hidden", variable=self.force_clear_attrs_var).pack(side="right", padx=(0, 18))
        ttk.Checkbutton(force_opts, text="فایل‌های قفل‌شده را برای حذف بعد از Restart زمان‌بندی کن", variable=self.force_reboot_var).pack(side="right", padx=(0, 18))

        force_info = ttk.Frame(force_tab, style="Card.TFrame", padding=14)
        force_info.pack(fill="x", padx=20, pady=(0, 8))
        ttk.Label(force_info, text="محافظت‌های اجباری", style="CardTitle.TLabel").pack(anchor="e")
        ttk.Label(
            force_info,
            text="ریشه درایو، Windows فعال، Program Files/ProgramData فعال و پروفایل کامل کاربر فعلی حتی با Administrator قابل انتخاب برای حذف نیستند. Windows.old یا Windows روی یک درایو قدیمی مجاز است.",
            style="Muted.TLabel", justify="right", wraplength=1180
        ).pack(fill="x", pady=(6, 0))

        force_log_wrap = ttk.Frame(force_tab, style="Card.TFrame", padding=10)
        force_log_wrap.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        force_log_head = ttk.Frame(force_log_wrap, style="Card.TFrame")
        force_log_head.pack(fill="x", pady=(0, 6))
        ttk.Label(force_log_head, text="گزارش عملیات و تشخیص مشکل", style="CardTitle.TLabel").pack(side="right")
        ttk.Button(force_log_head, text="کپی گزارش", command=self.copy_force_log, style="Ghost.TButton").pack(side="left")
        ttk.Button(force_log_head, text="باز کردن فایل گزارش", command=self.open_force_log_file, style="Ghost.TButton").pack(side="left", padx=(6, 0))
        self.force_log = tk.Text(
            force_log_wrap, height=14, bg="#0f141d", fg="#d1d5db", insertbackground="#ffffff",
            bd=0, relief="flat", wrap="word", font=("Segoe UI", 9)
        )
        self.force_log.pack(fill="both", expand=True)
        self.force_log.configure(state="disabled")

        force_bottom = ttk.Frame(force_tab, style="Top.TFrame", padding=(20, 12))
        force_bottom.pack(fill="x")
        self.force_delete_btn = ttk.Button(
            force_bottom, text="حذف اجباری این پوشه", command=self.start_force_delete,
            style="Danger.TButton", state="disabled"
        )
        self.force_delete_btn.pack(side="left")
        self.force_cancel_btn = ttk.Button(
            force_bottom, text="توقف", command=self.cancel_force_delete,
            style="Ghost.TButton", state="disabled"
        )
        self.force_cancel_btn.pack(side="left", padx=(8, 0))
        ttk.Checkbutton(
            force_bottom, text="می‌دانم حذف دائمی است و قابل بازگشت نیست", variable=self.force_confirm_var,
            command=self._refresh_force_delete_button
        ).pack(side="right")
        self.force_progress = ttk.Progressbar(force_tab, mode="indeterminate")
        self.force_progress.pack(fill="x", padx=20, pady=(0, 4))
        ttk.Label(force_tab, textvariable=self.force_status_var, style="Status.TLabel", anchor="e").pack(fill="x", padx=20, pady=(2, 10))
        self._update_force_admin_status()

        self._refresh_image_category_tree()
        if not PIL_AVAILABLE:
            self.img_status_var.set("برای گالری و دسته‌بندی تصاویر، Pillow را نصب کنید: pip install pillow")
            self.img_scan_btn.configure(state="disabled")

    def choose_folder(self):
        selected = filedialog.askdirectory(initialdir=self.folder_var.get() or os.getcwd())
        if selected:
            self.folder_var.set(selected)

    def _drain_old_events(self):
        try:
            while True:
                self.queue.get_nowait()
        except queue.Empty:
            pass

    def start_scan(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("پوشه نامعتبر", "لطفاً یک پوشه معتبر انتخاب کنید.")
            return
        if self.scan_thread and self.scan_thread.is_alive():
            return
        if self.image_scan_thread and self.image_scan_thread.is_alive():
            messagebox.showinfo("اسکن در حال اجرا", "برای جلوگیری از کندی شدید دیسک، ابتدا اسکن عکس‌ها را متوقف کنید.")
            return
        if self.safe_mode_var.get() and is_protected_path(folder):
            messagebox.showwarning(
                "مسیر سیستمی",
                "این مسیر جزو مسیرهای محافظت‌شده سیستم است.\nاگر واقعاً می‌خواهید فقط آن را بررسی کنید، حالت امن را خاموش کنید؛ حذف فایل‌های سیستمی همچنان مسدود می‌ماند."
            )
            return

        self._drain_old_events()
        self.stop_event.clear()
        self.pairs = []
        self.current_index = -1
        self.deleted_count = 0
        self.deleted_bytes = 0
        self.scan_active = True
        self.last_total_files = 0
        self.last_exact_groups = 0
        self.last_visual_groups = 0
        self._clear_results()
        self.scan_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.start(8)
        self.status_var.set("شروع اسکن زنده…")
        self.stats_var.set("در حال اسکن")

        self.scan_thread = threading.Thread(
            target=scan_duplicates,
            args=(
                folder,
                self.recursive_var.get(),
                self.visual_var.get(),
                self.safe_mode_var.get(),
                self.ignore_empty_var.get(),
                self.queue,
                self.stop_event,
            ),
            daemon=True,
        )
        self.scan_thread.start()

    def cancel_scan(self):
        if self.scan_thread and self.scan_thread.is_alive():
            self.stop_event.set()
            self.status_var.set("در حال لغو اسکن…")

    def _poll_queue(self):
        pair_added = False
        try:
            handled = 0
            # Keep UI responsive: process a bounded batch and refresh statistics once.
            while handled < 180:
                kind, payload = self.queue.get_nowait()
                handled += 1
                if kind == "status":
                    self.status_var.set(payload)
                elif kind == "progress":
                    _, text = payload
                    self.status_var.set(text)
                elif kind == "workers":
                    self.hash_workers, self.visual_workers, self.storage_type = payload
                elif kind == "live_stats":
                    total, exact_groups, visual_groups, _reclaimable = payload
                    self.last_total_files = total
                    self.last_exact_groups = exact_groups
                    self.last_visual_groups = visual_groups
                elif kind == "pair":
                    pair, size = payload
                    self._append_live_pair(pair, size, refresh=False)
                    pair_added = True
                elif kind == "delete_result":
                    self._handle_delete_result(payload)
                elif kind == "bulk_progress":
                    self._handle_bulk_progress(payload)
                elif kind == "bulk_done":
                    self._finish_bulk_delete(payload)
                elif kind == "done":
                    self._finish_scan(payload)
                elif kind == "cancelled":
                    self._scan_stopped("اسکن لغو شد؛ نتایج پیدا شده تا این لحظه باقی مانده‌اند.")
                elif kind == "error":
                    self._scan_stopped(f"اسکن با خطا متوقف شد: {payload}")
            if pair_added:
                self._refresh_stats()
        except queue.Empty:
            if pair_added:
                self._refresh_stats()
        try:
            delay = 18 if not self.queue.empty() else 35
            self.root.after(delay, self._poll_queue)
        except tk.TclError:
            pass

    def _refresh_stats(self):
        self.stats_var.set(
            f"{self.last_total_files:,} فایل • {self.last_exact_groups:,} گروه دقیق • "
            f"{self.last_visual_groups:,} تصویری • {self.visible_count:,} مورد باقی‌مانده "
            f"({human_size(self.visible_reclaimable)}) • حذف: {self.deleted_count:,}"
        )
        # Keep the red bulk-delete button synchronized with the LIVE list.
        # It is disabled while a bulk run is already in progress.
        try:
            self.bulk_trash_btn.configure(
                text=f"حذف همه تکراری‌های لیست ({self.visible_count:,})",
                state="normal" if self.visible_count > 0 and not self.bulk_delete_active else "disabled",
            )
        except (AttributeError, tk.TclError):
            pass

    def _register_row(self, index: int, pair: DuplicatePair, size: int):
        iid = str(index)
        nk = norm_path(pair.keeper)
        nc = norm_path(pair.candidate)
        self.path_rows[nk].add(iid)
        self.path_rows[nc].add(iid)
        self.group_rows[pair.group].add(iid)
        self.group_members[pair.group][nk] = pair.keeper
        self.group_members[pair.group][nc] = pair.candidate
        self.row_sizes[iid] = max(0, int(size or 0))
        self.visible_count += 1
        self.visible_reclaimable += self.row_sizes[iid]

    def _append_live_pair(self, pair: DuplicatePair, size: int, refresh: bool = True):
        nk = norm_path(pair.keeper)
        nc = norm_path(pair.candidate)
        if nk in self.deleted_paths or nc in self.deleted_paths or nk in self.delete_inflight or nc in self.delete_inflight:
            return
        if not os.path.exists(pair.keeper) or not os.path.exists(pair.candidate):
            return

        index = len(self.pairs)
        self.pairs.append(pair)
        iid = str(index)
        locked = self._is_candidate_locked(pair.candidate)
        kind_text = "دقیق" if pair.kind == "exact" else "تصویری"
        state_text = "محافظت‌شده" if locked else "آماده"
        tags = ("locked",) if locked else ()
        self.tree.insert(
            "", "end", iid=iid, text=os.path.basename(pair.candidate),
            values=(kind_text, human_size(size), state_text), tags=tags
        )
        self._register_row(index, pair, size)
        if refresh:
            self._refresh_stats()

        if self.visible_count == 1:
            self._select_iid(iid)

    def _scan_stopped(self, text):
        self.scan_active = False
        self.progress.stop()
        self.scan_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.status_var.set(text)
        self._refresh_stats()

    def _finish_scan(self, payload):
        result, elapsed, hash_workers, visual_workers = payload
        self.scan_active = False
        self.progress.stop()
        self.scan_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.hash_workers = hash_workers
        self.visual_workers = visual_workers
        self.storage_type = result.storage_type
        self.last_total_files = result.total_files
        self.last_exact_groups = result.exact_groups
        self.last_visual_groups = result.visual_groups
        self._refresh_stats()

        media = result.storage_type if result.storage_type in {"SSD", "HDD"} else "نامشخص"
        ignored_bits = []
        if result.skipped_protected:
            ignored_bits.append(f"{result.skipped_protected:,} مسیر/فایل سیستمی رد شد")
        if result.skipped_empty:
            ignored_bits.append(f"{result.skipped_empty:,} فایل خالی رد شد")
        if result.skipped_errors:
            ignored_bits.append(f"{result.skipped_errors:,} مورد بدون دسترسی")
        ignored_text = " • " + " • ".join(ignored_bits) if ignored_bits else ""

        if self.visible_count == 0:
            self.status_var.set(f"اسکن کامل شد؛ مورد تکراریِ قابل بررسی باقی نمانده • {elapsed:.1f} ثانیه • دیسک: {media}{ignored_text}")
            return

        self.status_var.set(
            f"اسکن کامل شد • {self.visible_count:,} مورد باقی مانده • {elapsed:.1f} ثانیه • "
            f"دیسک: {media} • Worker هش: {hash_workers} • تصویر: {visual_workers}{ignored_text}"
        )

    def _clear_results(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.keep_card.clear()
        self.candidate_card.clear()
        self.trash_btn.configure(state="disabled")
        self.bulk_trash_btn.configure(state="disabled")
        self.next_btn.configure(state="disabled")
        self.swap_btn.configure(state="disabled")
        self.path_rows.clear()
        self.group_rows.clear()
        self.group_members.clear()
        self.row_sizes.clear()
        self.deleted_paths.clear()
        self.locked_paths.clear()
        self.delete_problem_paths.clear()
        self.delete_inflight.clear()
        self.bulk_delete_active = False
        self.bulk_delete_total = 0
        self.bulk_delete_processed = 0
        self.bulk_delete_success = 0
        self.bulk_delete_failed = 0
        self.bulk_delete_skipped = 0
        self.visible_count = 0
        self.visible_reclaimable = 0

    def on_select_pair(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        try:
            index = int(selection[0])
        except ValueError:
            return
        self._show_pair(index)

    def _select_iid(self, iid: str):
        if not iid or not self.tree.exists(iid):
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        try:
            self._show_pair(int(iid))
        except ValueError:
            pass

    def _show_pair(self, index: int):
        iid = str(index)
        if index < 0 or index >= len(self.pairs) or not self.tree.exists(iid):
            return
        pair = self.pairs[index]

        if not os.path.exists(pair.keeper):
            self.deleted_paths.add(norm_path(pair.keeper))
            self._repair_rows_for_path(pair.keeper)
            self.status_var.set("یکی از فایل‌ها خارج از برنامه حذف/جابجا شده بود؛ لیست خودکار اصلاح شد.")
            return
        if not os.path.exists(pair.candidate):
            self.deleted_paths.add(norm_path(pair.candidate))
            self._repair_rows_for_path(pair.candidate)
            self.status_var.set("فایل حذف‌شده یا جابجا‌شده از لیست پاک شد.")
            return

        self.current_index = index
        self.keep_card.set_file(pair.keeper)
        self.candidate_card.set_file(pair.candidate)
        candidate_norm = norm_path(pair.candidate)
        locked = self._is_candidate_locked(pair.candidate)
        inflight = candidate_norm in self.delete_inflight
        self.trash_btn.configure(state="normal" if not locked and not inflight and not self.bulk_delete_active else "disabled")
        self.next_btn.configure(state="normal")
        self.swap_btn.configure(state="normal")

        if inflight:
            self.status_var.set("این فایل در حال انتقال به سطل زباله است؛ می‌توانید مورد بعدی را بررسی کنید.")
        elif locked:
            self.status_var.set("این فایل واقعاً داخل مسیر سیستمی/محافظت‌شده است و حذف آن مسدود است.")
        elif candidate_norm in self.delete_problem_paths:
            self.status_var.set("این فایل سیستمی نیست؛ حذف قبلی خطای دسترسی داده است. دکمه حذف فعال است و می‌توانید دوباره تلاش کنید.")
        elif pair.kind == "exact":
            self.status_var.set("تکراری دقیق: محتوای باینری دو فایل یکسان است.")
        else:
            self.status_var.set("شباهت تصویری: قبل از حذف، دو پیش‌نمایش را بررسی کنید.")

    def next_pair(self):
        if self.visible_count <= 0:
            return
        current = str(self.current_index)
        target = self.tree.next(current) if self.tree.exists(current) else ""
        if not target:
            self.status_var.set("به انتهای فهرست رسیدید.")
            return
        self._select_iid(target)

    def swap_pair(self):
        if self.current_index < 0 or self.current_index >= len(self.pairs):
            return
        iid = str(self.current_index)
        if not self.tree.exists(iid):
            return
        pair = self.pairs[self.current_index]
        if not os.path.exists(pair.keeper) or not os.path.exists(pair.candidate):
            self._show_pair(self.current_index)
            return
        pair.keeper, pair.candidate = pair.candidate, pair.keeper
        self._update_tree_row(self.current_index)
        self._show_pair(self.current_index)

    def _is_candidate_locked(self, path: str) -> bool:
        # IMPORTANT: only real OS/application paths are protected.
        # A normal user file that once returned Access denied must NOT become
        # permanently classified as "protected"; the user may close the app
        # using it, fix permissions and retry immediately.
        return is_protected_path(path)

    def _update_tree_row(self, index: int, forced_state: str = None, refresh: bool = True):
        iid = str(index)
        if index < 0 or index >= len(self.pairs) or not self.tree.exists(iid):
            return
        pair = self.pairs[index]
        try:
            new_size = os.path.getsize(pair.candidate)
        except OSError:
            new_size = 0
        old_size = self.row_sizes.get(iid, 0)
        self.row_sizes[iid] = new_size
        self.visible_reclaimable = max(0, self.visible_reclaimable - old_size + new_size)

        nc = norm_path(pair.candidate)
        if forced_state is not None:
            state = forced_state
        elif nc in self.delete_inflight:
            state = "در حال حذف"
        elif self._is_candidate_locked(pair.candidate):
            state = "محافظت‌شده"
        elif nc in self.delete_problem_paths:
            state = "خطای دسترسی — قابل تلاش مجدد"
        else:
            state = "آماده"
        kind_text = "دقیق" if pair.kind == "exact" else "تصویری"
        if state == "در حال حذف":
            tags = ("deleting",)
        elif state == "محافظت‌شده":
            tags = ("locked",)
        elif state.startswith("خطای دسترسی"):
            tags = ("warning",)
        else:
            tags = ()
        self.tree.item(iid, text=os.path.basename(pair.candidate), values=(kind_text, human_size(new_size), state), tags=tags)
        if refresh:
            self._refresh_stats()

    def _collect_bulk_candidates(self):
        """Snapshot unique candidate files that are visible RIGHT NOW.

        We only collect the candidate/right-side file of each visible pair. The keeper
        is never intentionally added. System-protected paths are skipped. The snapshot
        means duplicates discovered later by a still-running scan are not silently
        deleted; they remain visible for the user.
        """
        candidates = []
        seen = set()
        skipped_protected = 0
        skipped_inflight = 0
        missing_paths = []

        for iid in list(self.tree.get_children()):
            if not self.tree.exists(iid):
                continue
            try:
                idx = int(iid)
                pair = self.pairs[idx]
            except (ValueError, IndexError):
                continue

            path = pair.candidate
            npath = norm_path(path)
            if npath in seen:
                continue
            seen.add(npath)

            if not os.path.exists(path):
                missing_paths.append(path)
                continue
            if self._is_candidate_locked(path):
                skipped_protected += 1
                self.locked_paths.add(npath)
                self._mark_path_locked(npath)
                continue
            if npath in self.delete_inflight:
                skipped_inflight += 1
                continue

            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            candidates.append((path, size))

        # Clean up rows whose files disappeared outside the app, after iteration.
        for path in missing_paths:
            npath = norm_path(path)
            self.deleted_paths.add(npath)
            self._repair_rows_for_path(path)

        return candidates, skipped_protected, skipped_inflight

    def trash_all_visible(self):
        """Delete all current candidate files with one confirmation and one worker thread."""
        if self.bulk_delete_active:
            return

        candidates, skipped_protected, skipped_inflight = self._collect_bulk_candidates()
        if not candidates:
            if skipped_protected:
                self.status_var.set("مورد قابل حذف وجود ندارد؛ موارد باقی‌مانده سیستمی/محافظت‌شده هستند.")
            elif skipped_inflight:
                self.status_var.set("همه موارد قابل حذف فعلاً در حال پردازش هستند.")
            else:
                self.status_var.set("فایل تکراری قابل حذف در لیست وجود ندارد.")
            self._refresh_stats()
            return

        total_files = len(candidates)
        total_bytes = sum(max(0, int(size or 0)) for _, size in candidates)
        scan_note = (
            "\n\nاسکن هنوز ادامه دارد؛ فقط مواردی که همین الان در لیست هستند حذف می‌شوند. "
            "نتایج جدیدی که بعداً پیدا شوند باقی می‌مانند."
            if self.scan_active else ""
        )
        protected_note = (
            f"\n\n{skipped_protected:,} فایل/مسیر سیستمی محافظت‌شده حذف نمی‌شود."
            if skipped_protected else ""
        )

        if SEND2TRASH_AVAILABLE:
            question = (
                f"همه {total_files:,} فایل تکراری پیشنهادیِ فعلی به سطل زباله منتقل شوند؟\n\n"
                f"حجم تقریبی قابل آزادسازی: {human_size(total_bytes)}"
                f"{protected_note}{scan_note}"
            )
            title = "حذف همه تکراری‌های لیست"
        else:
            question = (
                f"send2trash نصب نیست؛ {total_files:,} فایل به‌صورت دائمی حذف می‌شوند.\n\n"
                f"حجم تقریبی: {human_size(total_bytes)}\n\n"
                "این عملیات قابل بازگردانی از سطل زباله نیست. ادامه می‌دهید؟"
                f"{protected_note}{scan_note}"
            )
            title = "تأیید حذف دائمی همه موارد"

        if not messagebox.askyesno(title, question, icon="warning"):
            return

        self.bulk_delete_active = True
        self.bulk_delete_total = total_files
        self.bulk_delete_processed = 0
        self.bulk_delete_success = 0
        self.bulk_delete_failed = 0
        self.bulk_delete_skipped = skipped_protected + skipped_inflight

        # Mark the whole snapshot as in-flight before the worker starts. This prevents
        # live scan results from re-adding the same path and prevents double clicks.
        for path, _size in candidates:
            npath = norm_path(path)
            self.delete_problem_paths.pop(npath, None)
            self.delete_inflight.add(npath)
            self._mark_path_deleting(npath)

        self.bulk_trash_btn.configure(state="disabled")
        self.trash_btn.configure(state="disabled")
        self.status_var.set(
            f"حذف گروهی شروع شد: 0/{total_files:,} • {human_size(total_bytes)} در صف • رابط و اسکن فعال می‌مانند"
        )
        self._refresh_stats()

        threading.Thread(
            target=self._bulk_delete_worker,
            args=(candidates,),
            daemon=True,
            name="dup-bulk-delete",
        ).start()

    def _bulk_delete_worker(self, candidates):
        total = len(candidates)
        for position, (path, size) in enumerate(candidates, start=1):
            # A file may have disappeared because the user/app removed it in parallel.
            if not os.path.exists(path):
                success, error, error_kind, retried = True, "", "", False
            else:
                success, error, error_kind, retried = self._delete_path_with_retry(path)

            self.queue.put(("delete_result", (path, size, success, error, error_kind, retried)))
            self.queue.put(("bulk_progress", (position, total, success, error_kind)))

        self.queue.put(("bulk_done", (total,)))

    def _handle_bulk_progress(self, payload):
        position, total, success, error_kind = payload
        self.bulk_delete_processed = max(self.bulk_delete_processed, int(position))
        if success:
            self.bulk_delete_success += 1
        else:
            self.bulk_delete_failed += 1
        if position == total or position % 20 == 0:
            self._refresh_stats()
            self.status_var.set(
                f"حذف گروهی: {position:,}/{total:,} • موفق: {self.bulk_delete_success:,} • "
                f"ناموفق: {self.bulk_delete_failed:,} • باقی‌مانده در لیست: {self.visible_count:,}"
            )

    def _finish_bulk_delete(self, payload):
        (total,) = payload
        self.bulk_delete_active = False
        self._refresh_stats()

        # Restore selection/buttons first; then write the final summary so _show_pair
        # does not immediately overwrite it.
        if 0 <= self.current_index < len(self.pairs) and self.tree.exists(str(self.current_index)):
            self._show_pair(self.current_index)
        elif self.visible_count > 0:
            children = self.tree.get_children()
            if children:
                self._select_iid(children[0])

        skipped_text = f" • ردشده/محافظت‌شده: {self.bulk_delete_skipped:,}" if self.bulk_delete_skipped else ""
        new_results_text = (
            " • اسکن هنوز ادامه دارد و ممکن است موارد جدید به لیست اضافه شوند"
            if self.scan_active else ""
        )
        self.status_var.set(
            f"حذف گروهی تمام شد • پردازش: {total:,} • موفق: {self.bulk_delete_success:,} • "
            f"ناموفق: {self.bulk_delete_failed:,}{skipped_text} • باقی‌مانده: {self.visible_count:,}"
            f"{new_results_text}"
        )

    def trash_candidate(self):
        if self.current_index < 0 or self.current_index >= len(self.pairs):
            return
        iid = str(self.current_index)
        if not self.tree.exists(iid):
            return
        pair = self.pairs[self.current_index]
        path = pair.candidate
        npath = norm_path(path)

        if npath in self.delete_inflight:
            return
        if not os.path.exists(path):
            self.delete_problem_paths.pop(npath, None)
            self.deleted_paths.add(npath)
            self._repair_rows_for_path(path)
            self.status_var.set("فایل دیگر وجود نداشت؛ مورد از لیست حذف شد.")
            return
        if self._is_candidate_locked(path):
            self.locked_paths.add(npath)
            self._mark_path_locked(npath)
            self.status_var.set("این مسیر واقعاً سیستمی است و برای جلوگیری از حذف خطرناک مسدود شده است.")
            return

        # A previous Access denied on a normal file is not permanent. Clear the
        # transient marker so every click can retry after the user closes the app
        # holding the file or permissions change.
        self.delete_problem_paths.pop(npath, None)

        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0

        if SEND2TRASH_AVAILABLE:
            if not self.fast_delete_var.get():
                if not messagebox.askyesno("تأیید حذف", f"این فایل به سطل زباله منتقل شود؟\n\n{path}", icon="warning"):
                    return
        else:
            question = "send2trash نصب نیست و حذف دائمی خواهد بود.\nآیا مطمئن هستید؟\n\n" + path
            if not messagebox.askyesno("تأیید حذف دائمی", question, icon="warning"):
                return

        self.delete_inflight.add(npath)
        self._mark_path_deleting(npath)
        self.status_var.set("در حال انتقال فایل به سطل زباله… اسکن و رابط کاربری متوقف نمی‌شوند.")

        if self.auto_next_var.get():
            self.next_pair()

        threading.Thread(target=self._delete_worker, args=(path, size), daemon=True).start()

    def _make_user_file_writable(self, path: str) -> bool:
        """Best-effort removal of the Windows read-only bit for NON-system files."""
        if is_protected_path(path):
            return False
        try:
            mode = os.stat(path).st_mode
            os.chmod(path, mode | stat.S_IWRITE | stat.S_IREAD)
            return True
        except Exception:
            return False

    def _delete_once(self, path: str):
        if SEND2TRASH_AVAILABLE:
            send2trash(path)
        else:
            os.remove(path)

    def _delete_path_with_retry(self, path: str):
        """Synchronous delete attempt used by both single and bulk workers."""
        success = False
        error = ""
        error_kind = ""
        retried = False

        try:
            self._delete_once(path)
            success = True
        except Exception as first_exc:
            first_winerror = getattr(first_exc, "winerror", None)
            first_permissionish = isinstance(first_exc, PermissionError) or first_winerror in {5, 32, 33}

            # Ordinary photos/documents often fail only because the read-only bit is
            # set. They must not be mislabeled as system-protected. Clear that bit
            # and retry once automatically.
            if first_permissionish and not is_protected_path(path) and os.path.exists(path):
                retried = self._make_user_file_writable(path)
                if retried:
                    try:
                        self._delete_once(path)
                        success = True
                    except Exception as second_exc:
                        first_exc = second_exc

            if not success:
                error = str(first_exc)
                winerror = getattr(first_exc, "winerror", None)
                if is_protected_path(path):
                    error_kind = "protected"
                elif winerror in {32, 33}:
                    error_kind = "in_use"
                elif isinstance(first_exc, PermissionError) or winerror == 5:
                    error_kind = "permission"
                else:
                    error_kind = "other"

        return success, error, error_kind, retried

    def _delete_worker(self, path: str, size: int):
        success, error, error_kind, retried = self._delete_path_with_retry(path)
        self.queue.put(("delete_result", (path, size, success, error, error_kind, retried)))

    def _handle_delete_result(self, payload):
        path, size, success, error, error_kind, retried = payload
        npath = norm_path(path)
        self.delete_inflight.discard(npath)

        if success:
            self.delete_problem_paths.pop(npath, None)
            self.deleted_paths.add(npath)
            self.deleted_count += 1
            self.deleted_bytes += max(0, int(size or 0))
            self._repair_rows_for_path(path, update_ui=not self.bulk_delete_active)
            retry_text = " • ویژگی Read-only خودکار اصلاح شد" if retried else ""
            if not self.bulk_delete_active:
                self.status_var.set(
                    f"حذف شد: {self.deleted_count:,} فایل • {human_size(self.deleted_bytes)} آزاد شده{retry_text} • اسکن/بررسی ادامه دارد"
                )
                self._refresh_stats()
            return

        short_error = error if len(error) <= 180 else error[:177] + "…"

        if error_kind == "protected":
            # This state is reserved only for actual Windows / Program Files / OS paths.
            self.locked_paths.add(npath)
            self._mark_path_locked(npath)
            self.status_var.set(
                "این فایل واقعاً داخل مسیر سیستمی/محافظت‌شده است و حذف آن توسط برنامه مسدود شده است."
            )
        elif error_kind == "in_use":
            self.delete_problem_paths[npath] = short_error
            self._mark_path_problem(npath)
            self.status_var.set(
                "این فایل سیستمی نیست؛ الان توسط یک برنامه دیگر باز/قفل است. برنامه مربوط را ببندید و دوباره «حذف» را بزنید."
            )
        elif error_kind == "permission":
            self.delete_problem_paths[npath] = short_error
            self._mark_path_problem(npath)
            self.status_var.set(
                "این فایل سیستمی نیست؛ Windows اجازه انتقالش به سطل زباله را نداد. Read-only خودکار بررسی شد. "
                "بعد از اصلاح دسترسی پوشه می‌توانید دوباره همان دکمه حذف را بزنید."
            )
        else:
            self.delete_problem_paths[npath] = short_error
            self._mark_path_problem(npath)
            self.status_var.set(f"حذف انجام نشد، ولی فایل محافظت‌شده علامت نخورد: {short_error}")

        # Refresh current buttons. Ordinary failed files stay deletable/retryable.
        if 0 <= self.current_index < len(self.pairs) and self.tree.exists(str(self.current_index)):
            self._show_pair(self.current_index)

    def _mark_path_deleting(self, normalized_path: str):
        for iid in list(self.path_rows.get(normalized_path, ())):
            if not self.tree.exists(iid):
                continue
            try:
                idx = int(iid)
                pair = self.pairs[idx]
            except (ValueError, IndexError):
                continue
            if norm_path(pair.candidate) == normalized_path:
                self._update_tree_row(idx, forced_state="در حال حذف", refresh=False)

    def _mark_path_locked(self, normalized_path: str):
        for iid in list(self.path_rows.get(normalized_path, ())):
            if not self.tree.exists(iid):
                continue
            try:
                idx = int(iid)
                pair = self.pairs[idx]
            except (ValueError, IndexError):
                continue
            if norm_path(pair.candidate) == normalized_path:
                self._update_tree_row(idx, forced_state="محافظت‌شده", refresh=False)

    def _mark_path_problem(self, normalized_path: str):
        for iid in list(self.path_rows.get(normalized_path, ())):
            if not self.tree.exists(iid):
                continue
            try:
                idx = int(iid)
                pair = self.pairs[idx]
            except (ValueError, IndexError):
                continue
            if norm_path(pair.candidate) == normalized_path:
                self._update_tree_row(idx, forced_state="خطای دسترسی — قابل تلاش مجدد", refresh=False)

    def _mark_path_ready(self, normalized_path: str):
        for iid in list(self.path_rows.get(normalized_path, ())):
            if not self.tree.exists(iid):
                continue
            try:
                idx = int(iid)
                pair = self.pairs[idx]
            except (ValueError, IndexError):
                continue
            if norm_path(pair.candidate) == normalized_path:
                self._update_tree_row(idx, refresh=False)

    def _remove_row(self, iid: str):
        if not self.tree.exists(iid):
            return
        try:
            idx = int(iid)
            pair = self.pairs[idx]
        except (ValueError, IndexError):
            self.tree.delete(iid)
            return

        nk = norm_path(pair.keeper)
        nc = norm_path(pair.candidate)
        self.path_rows[nk].discard(iid)
        self.path_rows[nc].discard(iid)
        if not self.path_rows[nk]:
            self.path_rows.pop(nk, None)
        if not self.path_rows[nc]:
            self.path_rows.pop(nc, None)
        self.group_rows[pair.group].discard(iid)
        if not self.group_rows[pair.group]:
            self.group_rows.pop(pair.group, None)

        self.visible_reclaimable = max(0, self.visible_reclaimable - self.row_sizes.pop(iid, 0))
        self.visible_count = max(0, self.visible_count - 1)
        self.tree.delete(iid)

    def _repair_rows_for_path(self, path: str, update_ui: bool = True):
        npath = norm_path(path)
        impacted = list(self.path_rows.get(npath, ()))
        if not impacted:
            return

        current_iid = str(self.current_index)
        current_was_impacted = current_iid in impacted and self.tree.exists(current_iid)
        preferred = ""
        if current_was_impacted:
            preferred = self.tree.next(current_iid) or self.tree.prev(current_iid)

        groups = set()
        for iid in impacted:
            try:
                groups.add(self.pairs[int(iid)].group)
            except (ValueError, IndexError):
                pass

        # Remove the vanished file from each group's member set first.
        for group in groups:
            self.group_members[group].pop(npath, None)

        for group in groups:
            row_ids = list(self.group_rows.get(group, ()))
            members = [
                original for np, original in self.group_members.get(group, {}).items()
                if np not in self.deleted_paths and os.path.exists(original)
            ]
            if len(members) >= 2:
                try:
                    canonical_keeper = choose_keeper(members)
                except Exception:
                    canonical_keeper = members[0]
                canonical_norm = norm_path(canonical_keeper)
            else:
                canonical_keeper = None
                canonical_norm = ""

            for iid in row_ids:
                if not self.tree.exists(iid):
                    continue
                try:
                    idx = int(iid)
                    pair = self.pairs[idx]
                except (ValueError, IndexError):
                    continue
                nk = norm_path(pair.keeper)
                nc = norm_path(pair.candidate)

                if nc == npath:
                    self._remove_row(iid)
                    continue
                if nk != npath:
                    continue
                if canonical_keeper is None or nc == canonical_norm:
                    self._remove_row(iid)
                    continue

                # Re-anchor the row to the group's surviving canonical keeper.
                self.path_rows[nk].discard(iid)
                if not self.path_rows[nk]:
                    self.path_rows.pop(nk, None)
                pair.keeper = canonical_keeper
                self.path_rows[canonical_norm].add(iid)

        self.path_rows.pop(npath, None)
        if update_ui:
            self._refresh_stats()

        if self.visible_count == 0:
            self.current_index = -1
            self.keep_card.clear()
            self.candidate_card.clear()
            self.trash_btn.configure(state="disabled")
            self.bulk_trash_btn.configure(state="disabled")
            self.next_btn.configure(state="disabled")
            self.swap_btn.configure(state="disabled")
            if update_ui and self.scan_active:
                self.status_var.set("همه نتایج فعلی بررسی شدند؛ اسکن هنوز ادامه دارد…")
            return

        if not update_ui:
            return

        if current_was_impacted:
            if preferred and self.tree.exists(preferred):
                self._select_iid(preferred)
            else:
                children = self.tree.get_children()
                if children:
                    self._select_iid(children[0])
        elif self.tree.exists(current_iid):
            self._show_pair(self.current_index)


    # ===================== Smart Photo Gallery =====================
    def start_image_scan(self):
        folder = self.folder_var.get().strip()
        if not PIL_AVAILABLE:
            messagebox.showwarning("Pillow", "برای مدیریت عکس‌ها ابتدا Pillow را نصب کنید.")
            return
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("پوشه نامعتبر", "لطفاً یک پوشه معتبر انتخاب کنید.")
            return
        if self.image_scan_thread and self.image_scan_thread.is_alive():
            return
        if self.scan_thread and self.scan_thread.is_alive():
            messagebox.showinfo(
                "اسکن در حال اجرا",
                "برای جلوگیری از فشار هم‌زمان روی هارد و گیرکردن رابط، ابتدا اسکن فایل‌های تکراری را متوقف کنید."
            )
            return
        if self.safe_mode_var.get() and is_protected_path(folder):
            messagebox.showwarning("مسیر سیستمی", "این مسیر در حالت امن اسکن نمی‌شود.")
            return

        try:
            while True:
                self.image_queue.get_nowait()
        except queue.Empty:
            pass
        self.image_stop_event.clear()
        self.image_records.clear()
        self.image_filtered.clear()
        self.image_keep_paths.clear()
        self.image_delete_errors.clear()
        self.image_page = 0
        self.image_scan_active = True
        self.image_filter_key = "cat:all"
        self.image_sidebar_dirty = True
        self.image_gallery_has_snapshot = False
        self.image_render_signature = None
        self.img_scan_btn.configure(state="disabled")
        self.img_cancel_btn.configure(state="normal")
        self.img_progress.start(9)
        self.img_status_var.set("شروع اسکن تصاویر…")
        self.img_stats_var.set("در حال اسکن")
        self._refresh_image_category_tree()
        self._render_image_page()

        self.image_scan_thread = threading.Thread(
            target=scan_image_library,
            args=(
                folder,
                self.recursive_var.get(),
                self.safe_mode_var.get(),
                self.img_deep_quality_var.get(),
                self.image_queue,
                self.image_stop_event,
            ),
            daemon=True,
            name="smart-photo-scan",
        )
        self.image_scan_thread.start()

    def cancel_image_scan(self):
        if self.image_scan_thread and self.image_scan_thread.is_alive():
            self.image_stop_event.set()
            self.img_status_var.set("در حال توقف اسکن عکس‌ها…")

    def _poll_image_queue(self):
        """Drain gallery events without continuously destroying/rebuilding the grid.

        V3.0 refreshed the entire Treeview + thumbnail page for every incoming metadata
        batch.  A category-selection virtual event could then schedule another refresh.
        On a large folder this repeatedly invalidated thumbnail jobs, producing the
        blank/flickering gallery seen by the user.  V3.2 keeps that stable live snapshot
        while scanning and performs one final refresh when the scan finishes.
        """
        refresh_gallery = False
        sidebar_changed = False
        try:
            handled = 0
            while handled < 180:
                kind, payload = self.image_queue.get_nowait()
                handled += 1
                if kind == "image_batch":
                    self.image_records.extend(payload)
                    sidebar_changed = True
                    # Show a useful first page quickly, then keep it stable while the disk
                    # scan continues. New results are merged on final refresh (or manually).
                    if (not self.image_gallery_has_snapshot and
                            len(self.image_records) >= min(36, self.image_page_size)):
                        self.image_gallery_has_snapshot = True
                        refresh_gallery = True
                elif kind == "image_status":
                    self.img_status_var.set(payload)
                elif kind == "image_progress":
                    image_seen, processed, text = payload
                    self.img_stats_var.set(f"{image_seen:,} عکس پیدا شد • {processed:,} تحلیل شد")
                    if self.image_gallery_has_snapshot:
                        text += " • نمایش فعلی ثابت نگه داشته شده تا پرش نکند"
                    self.img_status_var.set(text)
                elif kind == "image_done":
                    self._finish_image_scan(payload)
                    sidebar_changed = True
                    refresh_gallery = True
                    self.image_gallery_has_snapshot = True
                elif kind == "image_cancelled":
                    self.image_scan_active = False
                    self.img_progress.stop()
                    self.img_scan_btn.configure(state="normal")
                    self.img_cancel_btn.configure(state="disabled")
                    self.img_status_var.set("اسکن عکس‌ها لغو شد؛ نتایج پیدا شده تا این لحظه باقی مانده‌اند.")
                    sidebar_changed = True
                    refresh_gallery = True
                elif kind == "image_error":
                    self.image_scan_active = False
                    self.img_progress.stop()
                    self.img_scan_btn.configure(state="normal")
                    self.img_cancel_btn.configure(state="disabled")
                    self.img_status_var.set(f"خطای اسکن عکس‌ها: {payload}")
                elif kind == "thumb_ready":
                    self._handle_thumb_ready(payload)
                elif kind == "image_delete_batch":
                    self._handle_image_delete_batch(payload)
                    sidebar_changed = True
                    refresh_gallery = True
                elif kind == "image_delete_done":
                    self._finish_image_delete(payload)
                    sidebar_changed = True
                    refresh_gallery = True

            if sidebar_changed:
                self.image_sidebar_dirty = True
            if refresh_gallery:
                self._schedule_image_filter_refresh(delay=90 if self.image_scan_active else 30)
        except queue.Empty:
            if sidebar_changed:
                self.image_sidebar_dirty = True
            if refresh_gallery:
                self._schedule_image_filter_refresh(delay=90 if self.image_scan_active else 30)
        try:
            self.root.after(55 if self.image_queue.empty() else 18, self._poll_image_queue)
        except tk.TclError:
            pass

    def _finish_image_scan(self, info):
        self.image_scan_active = False
        self.img_progress.stop()
        self.img_scan_btn.configure(state="normal")
        self.img_cancel_btn.configure(state="disabled")
        self.img_stats_var.set(f"{len(self.image_records):,} عکس")
        ignored = f" • ردشده سیستمی: {info['skipped_protected']:,}" if info.get("skipped_protected") else ""
        errors = f" • خطا: {info['errors']:,}" if info.get("errors") else ""
        self.img_status_var.set(
            f"اسکن عکس‌ها تمام شد • {len(self.image_records):,} تصویر • {info['elapsed']:.1f} ثانیه • "
            f"دیسک: {info['storage']} • Worker: {info['workers']}{ignored}{errors}"
        )

    def _schedule_image_filter_refresh(self, force_page_reset=False, delay=None):
        if force_page_reset:
            self.image_page = 0
        if self.image_render_scheduled:
            return
        self.image_render_scheduled = True
        def run():
            self.image_render_scheduled = False
            self._refresh_image_filters()
        if delay is None:
            # User filter/search actions should feel immediate. Automatic live-scan updates
            # are intentionally calmer so thumbnail decoding can finish.
            delay = 120 if self.image_scan_active else 45
        try:
            self.root.after(delay, run)
        except tk.TclError:
            self.image_render_scheduled = False

    def _refresh_image_category_tree(self):
        if not hasattr(self, "img_category_tree"):
            return
        if not self.image_sidebar_dirty and self.img_category_tree.get_children():
            return

        self.image_category_rebuilding = True
        current = self.image_filter_key
        tree = self.img_category_tree
        # Critical V3.2 fix: rebuilding + selection_set used to emit another
        # <<TreeviewSelect>>, which scheduled another rebuild and created a refresh loop.
        try:
            tree.unbind("<<TreeviewSelect>>")
        except tk.TclError:
            pass
        try:
            for iid in tree.get_children():
                tree.delete(iid)

            cat_counts = defaultdict(int)
            res_counts = defaultdict(int)
            size_counts = defaultdict(int)
            orient_counts = defaultdict(int)
            for rec in self.image_records:
                cat_counts[rec.category] += 1
                res_counts[rec.resolution_bucket] += 1
                size_counts[rec.filesize_bucket] += 1
                orient_counts[rec.orientation] += 1

            content = tree.insert("", "end", iid="head:content", text="محتوا")
            tree.insert(content, "end", iid="cat:all", text=f"همه عکس‌ها ({len(self.image_records):,})")
            for key in ("screenshot", "ui_asset", "cache", "quality", "lowres", "personal", "other"):
                tree.insert(content, "end", iid=f"cat:{key}", text=f"{IMAGE_CATEGORY_LABELS[key]} ({cat_counts[key]:,})")

            res_head = tree.insert("", "end", iid="head:res", text="رزولوشن")
            for value in RESOLUTION_BUCKETS:
                tree.insert(res_head, "end", iid=f"res:{value}", text=f"{value} ({res_counts[value]:,})")

            size_head = tree.insert("", "end", iid="head:size", text="حجم فایل")
            for value in FILESIZE_BUCKETS:
                tree.insert(size_head, "end", iid=f"size:{value}", text=f"{value} ({size_counts[value]:,})")

            orient_head = tree.insert("", "end", iid="head:orient", text="جهت تصویر")
            for value in ORIENTATION_LABELS:
                tree.insert(orient_head, "end", iid=f"orient:{value}", text=f"{value} ({orient_counts[value]:,})")

            tree.item(content, open=True)
            tree.item(res_head, open=True)
            tree.item(size_head, open=True)
            tree.item(orient_head, open=False)
            if tree.exists(current):
                tree.selection_set(current)
                tree.focus(current)
            elif tree.exists("cat:all"):
                self.image_filter_key = "cat:all"
                tree.selection_set("cat:all")
            self.image_sidebar_dirty = False
        finally:
            tree.bind("<<TreeviewSelect>>", self._on_image_category_select)
            # Keep the guard through the current Tk event turn as extra protection
            # against a queued virtual selection event.
            try:
                self.root.after_idle(lambda: setattr(self, "image_category_rebuilding", False))
            except tk.TclError:
                self.image_category_rebuilding = False

    def _on_image_category_select(self, _event=None):
        if self.image_category_rebuilding:
            return
        sel = self.img_category_tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("head:"):
            return
        self.image_filter_key = iid
        self.image_page = 0
        self._schedule_image_filter_refresh(force_page_reset=True)

    def _manual_refresh_image_gallery(self):
        """User-requested snapshot refresh while a long scan is still running."""
        self.image_sidebar_dirty = True
        self.image_gallery_has_snapshot = True
        self._refresh_image_filters()
        if self.image_scan_active:
            self.img_status_var.set("نمایش با نتایج فعلی به‌روزرسانی شد؛ اسکن در پس‌زمینه ادامه دارد و صفحه دیگر خودکار نمی‌پرد.")

    def _record_matches_sidebar(self, rec: ImageRecord) -> bool:
        key = self.image_filter_key
        if key == "cat:all":
            return True
        if key.startswith("cat:"):
            return rec.category == key.split(":", 1)[1]
        if key.startswith("res:"):
            return rec.resolution_bucket == key.split(":", 1)[1]
        if key.startswith("size:"):
            return rec.filesize_bucket == key.split(":", 1)[1]
        if key.startswith("orient:"):
            return rec.orientation == key.split(":", 1)[1]
        return True

    def _refresh_image_filters(self):
        self._refresh_image_category_tree()
        query = self.img_search_var.get().strip().lower()
        res_filter = self.img_resolution_var.get()
        size_filter = self.img_filesize_var.get()
        orient_filter = self.img_orientation_var.get()

        filtered = []
        for rec in self.image_records:
            if not self._record_matches_sidebar(rec):
                continue
            if res_filter != "همه رزولوشن‌ها" and rec.resolution_bucket != res_filter:
                continue
            if size_filter != "همه حجم‌ها" and rec.filesize_bucket != size_filter:
                continue
            if orient_filter != "همه جهت‌ها" and rec.orientation != orient_filter:
                continue
            if query and query not in os.path.basename(rec.path).lower() and query not in rec.path.lower():
                continue
            filtered.append(rec)

        sort_mode = self.img_sort_var.get()
        if sort_mode == "قدیمی‌ترین":
            filtered.sort(key=lambda r: r.mtime)
        elif sort_mode == "بزرگ‌ترین فایل":
            filtered.sort(key=lambda r: r.size, reverse=True)
        elif sort_mode == "بیشترین رزولوشن":
            filtered.sort(key=lambda r: r.width * r.height, reverse=True)
        elif sort_mode == "نام":
            filtered.sort(key=lambda r: os.path.basename(r.path).lower())
        else:
            filtered.sort(key=lambda r: r.mtime, reverse=True)

        self.image_filtered = filtered
        max_page = max(0, (len(filtered) - 1) // self.image_page_size)
        self.image_page = min(self.image_page, max_page)
        keep_count = sum(1 for rec in filtered if norm_path(rec.path) in self.image_keep_paths)
        total_bytes = sum(rec.size for rec in filtered)
        self.img_stats_var.set(
            f"{len(self.image_records):,} عکس • فیلتر: {len(filtered):,} • نگه‌داری: {keep_count:,} • {human_size(total_bytes)}"
        )
        self._render_image_page()

    def _thumbnail_dimensions(self):
        mode = self.img_thumb_size_var.get()
        if mode == "کوچک":
            return (112, 84), 138
        if mode == "بزرگ":
            return (208, 156), 238
        return (154, 116), 184

    def _on_gallery_canvas_resize(self, event):
        try:
            self.img_canvas.itemconfigure(self.img_canvas_window, width=event.width)
        except tk.TclError:
            pass

    def _render_image_page(self):
        if not hasattr(self, "img_grid_frame"):
            return

        total = len(self.image_filtered)
        pages = max(1, (total + self.image_page_size - 1) // self.image_page_size) if total else 0
        if total:
            self.image_page = min(self.image_page, pages - 1)
        else:
            self.image_page = 0
        start = self.image_page * self.image_page_size
        end = min(total, start + self.image_page_size)
        page_records = self.image_filtered[start:end]
        target, card_width = self._thumbnail_dimensions()
        render_signature = (
            self.image_page,
            target,
            tuple(norm_path(rec.path) for rec in page_records),
        )
        self.img_page_label.configure(text=f"صفحه {self.image_page + 1 if pages else 0}/{pages}")
        self.img_prev_btn.configure(state="normal" if self.image_page > 0 else "disabled")
        self.img_next_btn.configure(state="normal" if pages and self.image_page < pages - 1 else "disabled")
        self.img_delete_btn.configure(state="normal" if total and not self.image_delete_active else "disabled")

        # If only counters changed, preserve the exact same card widgets and thumbnails.
        # This is the key anti-flicker path: no token invalidation, no widget destruction.
        if (render_signature == self.image_render_signature and
                (page_records and self.image_thumb_widgets)):
            return

        self.image_render_signature = render_signature
        self.image_thumb_token += 1
        token = self.image_thumb_token
        self.image_thumb_widgets.clear()
        self.image_thumb_photos.clear()
        for child in self.img_grid_frame.winfo_children():
            child.destroy()

        if not page_records:
            ttk.Label(
                self.img_grid_frame,
                text="عکسی مطابق فیلتر فعلی وجود ندارد.",
                style="Muted.TLabel",
            ).grid(row=0, column=0, padx=20, pady=30)
            return

        canvas_width = max(700, self.img_canvas.winfo_width())
        columns = max(3, min(10, canvas_width // card_width))

        for col in range(columns):
            self.img_grid_frame.columnconfigure(col, weight=1)

        for local_index, rec in enumerate(page_records):
            row, col = divmod(local_index, columns)
            card = ttk.Frame(self.img_grid_frame, style="Card.TFrame", padding=6, width=card_width)
            card.grid(row=row, column=col, padx=5, pady=5, sticky="n")
            card.grid_propagate(False)
            card.configure(width=card_width, height=target[1] + 92)
            # IMPORTANT: never put width/height on a tk.Label that later receives an
            # image. Tk interprets those options as pixel constraints once an image is
            # assigned; V3.1 therefore clipped a 154×116 thumbnail into a ~19×6 strip.
            # A fixed-size holder gives us the exact thumbnail viewport without cropping.
            thumb_holder = tk.Frame(
                card, bg="#151a23", bd=0, highlightthickness=0,
                width=target[0], height=target[1]
            )
            thumb_holder.pack(anchor="center")
            thumb_holder.pack_propagate(False)
            img_label = tk.Label(
                thumb_holder, text="در حال بارگذاری…", bg="#151a23", fg="#9ca3af",
                bd=0, anchor="center", justify="center"
            )
            img_label.pack(fill="both", expand=True)
            img_label.bind("<Double-1>", lambda e, p=rec.path: open_with_default_app(p))
            img_label.bind("<Button-3>", lambda e, p=rec.path: reveal_in_folder(p))
            self.image_thumb_widgets[norm_path(rec.path)] = img_label

            keep_var = tk.BooleanVar(value=norm_path(rec.path) in self.image_keep_paths)
            cb = ttk.Checkbutton(
                card, text="نگه دار", variable=keep_var,
                command=lambda p=rec.path, v=keep_var: self._set_image_keep(p, v.get())
            )
            cb.pack(anchor="e", pady=(4, 0))
            name = os.path.basename(rec.path)
            if len(name) > 24:
                name = name[:11] + "…" + name[-10:]
            ttk.Label(card, text=name, style="FileName.TLabel", anchor="e").pack(fill="x")
            meta = f"{rec.width}×{rec.height} • {human_size(rec.size)}"
            ttk.Label(card, text=meta, style="Muted.TLabel", anchor="e").pack(fill="x")
            flag_text = " • ".join(rec.flags[:2]) if rec.flags else IMAGE_CATEGORY_LABELS.get(rec.category, "تصویر")
            ttk.Label(card, text=flag_text, style="Muted.TLabel", anchor="e").pack(fill="x")
            self._request_gallery_thumbnail(rec.path, target, token)

        try:
            self.img_canvas.yview_moveto(0)
        except tk.TclError:
            pass

    def _request_gallery_thumbnail(self, path: str, target: tuple, token: int):
        key = (norm_path(path), target)
        cached = self.image_thumb_cache.get(key)
        if cached is not None:
            self.image_thumb_cache.move_to_end(key)
            label = self.image_thumb_widgets.get(norm_path(path))
            if label is not None and label.winfo_exists():
                label.configure(image=cached, text="")
                self.image_thumb_photos[norm_path(path)] = cached
            return

        future = self.image_thumb_executor.submit(make_gallery_thumbnail, path, target)
        def done(fut):
            try:
                pil_img = fut.result()
            except Exception:
                pil_img = None
            self.image_queue.put(("thumb_ready", (token, path, target, pil_img)))
        future.add_done_callback(done)

    def _handle_thumb_ready(self, payload):
        token, path, target, pil_img = payload
        if pil_img is None:
            return
        try:
            # Cache the completed thumbnail even if an older render token requested it.
            # If the same file appears again we can show it instantly instead of decoding twice.
            photo = ImageTk.PhotoImage(pil_img)
            key = (norm_path(path), target)
            self.image_thumb_cache[key] = photo
            self.image_thumb_cache.move_to_end(key)
            while len(self.image_thumb_cache) > self.image_thumb_cache_limit:
                self.image_thumb_cache.popitem(last=False)

            if token != self.image_thumb_token:
                return
            label = self.image_thumb_widgets.get(norm_path(path))
            if label is None or not label.winfo_exists():
                return
            self.image_thumb_photos[norm_path(path)] = photo
            label.configure(image=photo, text="")
        except tk.TclError:
            pass

    def _set_image_keep(self, path: str, keep: bool):
        npath = norm_path(path)
        if keep:
            self.image_keep_paths.add(npath)
        else:
            self.image_keep_paths.discard(npath)
        # Updating the count is cheap; do not rebuild thumbnails for every click.
        keep_count = sum(1 for rec in self.image_filtered if norm_path(rec.path) in self.image_keep_paths)
        self.img_stats_var.set(
            f"{len(self.image_records):,} عکس • فیلتر: {len(self.image_filtered):,} • نگه‌داری: {keep_count:,}"
        )

    def change_image_page(self, delta: int):
        total = len(self.image_filtered)
        pages = max(1, (total + self.image_page_size - 1) // self.image_page_size) if total else 0
        if not pages:
            return
        self.image_page = max(0, min(pages - 1, self.image_page + delta))
        self._render_image_page()

    def _filtered_records_for_action(self):
        if self.img_delete_page_only_var.get():
            start = self.image_page * self.image_page_size
            return self.image_filtered[start:start + self.image_page_size]
        return list(self.image_filtered)

    def keep_all_filtered(self):
        for rec in self._filtered_records_for_action():
            self.image_keep_paths.add(norm_path(rec.path))
        self._refresh_image_filters()

    def clear_keep_filtered(self):
        for rec in self._filtered_records_for_action():
            self.image_keep_paths.discard(norm_path(rec.path))
        self._refresh_image_filters()

    def delete_filtered_images(self):
        if self.image_delete_active:
            return
        records = self._filtered_records_for_action()
        candidates = []
        protected = 0
        for rec in records:
            npath = norm_path(rec.path)
            if npath in self.image_keep_paths:
                continue
            if not os.path.exists(rec.path):
                continue
            if is_protected_path(rec.path):
                protected += 1
                continue
            candidates.append(rec)

        if not candidates:
            self.img_status_var.set("در فیلتر فعلی فایل قابل حذف وجود ندارد یا همه تیک «نگه دار» دارند.")
            return
        total_bytes = sum(r.size for r in candidates)
        scope = "همین صفحه" if self.img_delete_page_only_var.get() else "کل فیلتر فعلی"
        if SEND2TRASH_AVAILABLE:
            question = (
                f"{len(candidates):,} عکس از {scope} به سطل زباله منتقل شود؟\n\n"
                f"حجم: {human_size(total_bytes)}\n\n"
                "عکس‌هایی که تیک «نگه دار» دارند حذف نمی‌شوند."
            )
        else:
            question = (
                f"send2trash نصب نیست و {len(candidates):,} عکس به‌صورت دائمی حذف می‌شوند.\n\n"
                f"حجم: {human_size(total_bytes)}\n\nادامه می‌دهید؟"
            )
        if protected:
            question += f"\n\n{protected:,} فایل سیستمی/محافظت‌شده رد می‌شود."
        if not messagebox.askyesno("پاکسازی عکس‌ها", question, icon="warning"):
            return

        self.image_delete_active = True
        self.img_delete_btn.configure(state="disabled")
        self.img_status_var.set(f"حذف عکس‌ها شروع شد: 0/{len(candidates):,}")
        threading.Thread(
            target=self._image_delete_worker,
            args=(candidates,),
            daemon=True,
            name="photo-bulk-delete",
        ).start()

    def _image_delete_worker(self, records):
        batch = []
        success_count = 0
        fail_count = 0
        total = len(records)
        for pos, rec in enumerate(records, start=1):
            if not os.path.exists(rec.path):
                success, error = True, ""
            else:
                success, error, _kind, _retried = self._delete_path_with_retry(rec.path)
            if success:
                success_count += 1
            else:
                fail_count += 1
            batch.append((rec.path, success, error))
            if len(batch) >= 20 or pos == total:
                self.image_queue.put(("image_delete_batch", (pos, total, batch, success_count, fail_count)))
                batch = []
        self.image_queue.put(("image_delete_done", (total, success_count, fail_count)))

    def _handle_image_delete_batch(self, payload):
        pos, total, results, success_count, fail_count = payload
        removed = {norm_path(path) for path, success, _error in results if success}
        if removed:
            self.image_records = [r for r in self.image_records if norm_path(r.path) not in removed]
            self.image_keep_paths.difference_update(removed)
            for np in removed:
                self.image_delete_errors.pop(np, None)
        for path, success, error in results:
            if not success:
                self.image_delete_errors[norm_path(path)] = error
        self.img_status_var.set(
            f"حذف عکس‌ها: {pos:,}/{total:,} • موفق: {success_count:,} • ناموفق: {fail_count:,}"
        )

    def _finish_image_delete(self, payload):
        total, success_count, fail_count = payload
        self.image_delete_active = False
        self.img_status_var.set(
            f"پاکسازی تمام شد • پردازش: {total:,} • حذف‌شده: {success_count:,} • ناموفق: {fail_count:,}"
        )
        self._refresh_image_filters()


    # ======================== Force folder delete ========================
    def _update_force_admin_status(self):
        if not sys.platform.startswith("win"):
            self.force_admin_var.set("وضعیت: این قابلیت فقط برای Windows است")
            return
        if is_windows_admin():
            self.force_admin_var.set("Administrator: فعال — امکان takeown و تغییر مجوزها وجود دارد")
        else:
            self.force_admin_var.set("Administrator: غیرفعال — برای پوشه‌های Access Denied باید برنامه را با دسترسی Administrator اجرا کنید")

    def _append_force_log(self, text):
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {text}\n"
        self.force_log_lines.append(line.rstrip("\n"))
        if len(self.force_log_lines) > 8000:
            self.force_log_lines = self.force_log_lines[-6000:]
        if self.force_log_file:
            try:
                with open(self.force_log_file, "a", encoding="utf-8") as fp:
                    fp.write(line)
            except Exception:
                pass
        if not hasattr(self, "force_log"):
            return
        try:
            self.force_log.configure(state="normal")
            self.force_log.insert("end", line)
            self.force_log.see("end")
            self.force_log.configure(state="disabled")
        except Exception:
            pass

    def _start_force_log_file(self, folder):
        self.force_log_lines = []
        self.force_log_file = None
        try:
            base = os.environ.get("LOCALAPPDATA") or os.path.join(str(Path.home()), ".nexaclean-pro")
            log_dir = os.path.join(base, "NexaClean Pro", "logs")
            os.makedirs(log_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            drive = os.path.splitdrive(os.path.abspath(folder))[0].replace(":", "") or "folder"
            self.force_log_file = os.path.join(log_dir, f"force-delete-{drive}-{stamp}.log")
            with open(self.force_log_file, "w", encoding="utf-8") as fp:
                fp.write("NexaClean Pro 3.5 - Smart Force Delete log\n")
                fp.write(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                fp.write(f"Target: {folder}\n\n")
        except Exception:
            self.force_log_file = None

    def copy_force_log(self):
        data = "\n".join(self.force_log_lines).strip()
        if not data:
            self.force_status_var.set("هنوز گزارشی برای کپی وجود ندارد.")
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(data)
            self.force_status_var.set("گزارش عملیات در Clipboard کپی شد.")
        except Exception as exc:
            messagebox.showerror("خطا", f"کپی گزارش ممکن نشد:\n{exc}")

    def open_force_log_file(self):
        if self.force_log_file and os.path.exists(self.force_log_file):
            open_with_default_app(self.force_log_file)
        else:
            self.force_status_var.set("فایل گزارش هنوز ساخته نشده است؛ ابتدا عملیات حذف را شروع کنید.")

    @staticmethod
    def _force_error_kind(exc):
        winerror = getattr(exc, "winerror", None)
        err_no = getattr(exc, "errno", None)
        msg = str(exc).lower()
        if isinstance(exc, FileNotFoundError) or winerror in (2, 3):
            return "missing"
        if winerror in (32, 33) or "being used by another process" in msg or "used by another process" in msg:
            return "locked"
        if isinstance(exc, PermissionError) or winerror in (5, 1314) or err_no in (13, 1):
            return "access_denied"
        if winerror == 206 or "filename or extension is too long" in msg:
            return "path_too_long"
        if winerror in (145, 183) or "directory is not empty" in msg:
            return "dir_not_empty"
        if winerror in (4390, 4392) or "reparse" in msg:
            return "reparse"
        return "other"

    def _force_diagnosis_text(self, stats):
        kinds = stats.get("errors_by_kind", {}) if stats else {}
        parts = []
        access = kinds.get("access_denied", 0)
        locked = kinds.get("locked", 0)
        longp = kinds.get("path_too_long", 0)
        notempty = kinds.get("dir_not_empty", 0)
        other = kinds.get("other", 0)
        if access:
            parts.append(f"Access Denied: {access:,} — نیاز به اصلاح مالکیت/ACL")
        if locked:
            parts.append(f"فایل در حال استفاده: {locked:,} — takeown معمولاً کمکی نمی‌کند؛ Restart مؤثرتر است")
        if longp:
            parts.append(f"مسیر خیلی طولانی: {longp:,} — موتور Long Path فعال است")
        if notempty:
            parts.append(f"پوشه خالی‌نشده: {notempty:,} — معمولاً نتیجه فایل قفل/بدون‌دسترسی داخل آن است")
        if other:
            parts.append(f"خطای دیگر: {other:,}")
        if not parts and stats and stats.get("errors", 0):
            parts.append(f"خطاهای ثبت‌شده: {stats.get('errors', 0):,}")
        return " | ".join(parts) if parts else "خطای مؤثر خاصی تشخیص داده نشد."

    def choose_force_folder(self):
        initial = self.force_folder_var.get().strip() or self.folder_var.get().strip() or os.getcwd()
        selected = filedialog.askdirectory(initialdir=initial)
        if selected:
            self.force_folder_var.set(selected)
            self.force_confirm_var.set(False)
            self.inspect_force_folder()

    def inspect_force_folder(self):
        folder = self.force_folder_var.get().strip()
        reason = force_delete_block_reason(folder)
        self._update_force_admin_status()
        if reason:
            self.force_status_var.set("مسدود: " + reason)
            self._append_force_log("مسیر رد شد: " + reason)
            self.force_delete_btn.configure(state="disabled")
            return False
        if not folder:
            self.force_status_var.set("پوشه‌ای انتخاب نشده است.")
            self.force_delete_btn.configure(state="disabled")
            return False

        kind = "پوشه معمولی"
        if looks_like_old_windows_folder(folder):
            kind = "نصب/بقایای قدیمی Windows"
        try:
            st = os.stat(folder)
            modified = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
        except Exception:
            modified = "نامشخص"
        self.force_status_var.set(
            f"مجاز برای بررسی حذف • نوع: {kind} • آخرین تغییر: {modified}"
        )
        self._append_force_log(f"مسیر انتخاب شد: {folder} | تشخیص: {kind}")
        self._refresh_force_delete_button()
        return True

    def _refresh_force_delete_button(self):
        if not hasattr(self, "force_delete_btn"):
            return
        folder = self.force_folder_var.get().strip()
        allowed = bool(folder) and not force_delete_block_reason(folder)
        enabled = allowed and self.force_confirm_var.get() and not self.force_delete_active
        self.force_delete_btn.configure(state="normal" if enabled else "disabled")

    def relaunch_as_admin(self):
        if not sys.platform.startswith("win"):
            messagebox.showinfo("Administrator", "این قابلیت مخصوص Windows است.")
            return
        if is_windows_admin():
            messagebox.showinfo("Administrator", "برنامه همین حالا با دسترسی Administrator اجرا شده است.")
            return
        try:
            if getattr(sys, "frozen", False):
                executable = sys.executable
                params = subprocess.list2cmdline(sys.argv[1:])
            else:
                executable = sys.executable
                params = subprocess.list2cmdline([os.path.abspath(sys.argv[0]), *sys.argv[1:]])
            rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, os.getcwd(), 1)
            if int(rc) <= 32:
                raise OSError(f"ShellExecuteW={rc}")
            self._append_force_log("نسخه Administrator درخواست شد؛ این پنجره بسته می‌شود.")
            self.root.after(500, self.root.destroy)
        except Exception as exc:
            messagebox.showerror("خطا", f"اجرای Administrator ممکن نشد:\n{exc}")

    def start_force_delete(self):
        if self.force_delete_active:
            return
        folder = self.force_folder_var.get().strip()
        reason = force_delete_block_reason(folder)
        if reason:
            messagebox.showerror("مسیر مسدود", reason)
            self.inspect_force_folder()
            return
        if not self.force_confirm_var.get():
            messagebox.showwarning("تأیید لازم است", "ابتدا تیک تأیید حذف دائمی را فعال کنید.")
            return
        if sys.platform.startswith("win") and not is_windows_admin() and self.force_takeown_var.get():
            answer = messagebox.askyesno(
                "نیاز به Administrator",
                "برای گرفتن مالکیت و حذف پوشه‌های قفل‌شده، برنامه باید با Administrator اجرا شود.\n\nالان نسخه Administrator اجرا شود؟"
            )
            if answer:
                self.relaunch_as_admin()
            return

        label = os.path.basename(os.path.abspath(folder).rstrip("\\/")) or folder
        old_note = "\n\nاین مسیر شبیه نصب قدیمی Windows تشخیص داده شده است." if looks_like_old_windows_folder(folder) else ""
        if not messagebox.askyesno(
            "تأیید حذف دائمی",
            f"پوشه زیر به‌صورت دائمی حذف می‌شود و وارد Recycle Bin نمی‌شود:\n\n{folder}{old_note}\n\nادامه می‌دهید؟",
            icon="warning"
        ):
            return

        self.force_delete_stop_event.clear()
        self.force_delete_active = True
        self.force_delete_btn.configure(state="disabled")
        self.force_cancel_btn.configure(state="normal")
        self.force_progress.start(10)
        self.force_status_var.set(f"شروع حذف اجباری: {label}")
        self._start_force_log_file(folder)
        self._append_force_log("شروع عملیات حذف اجباری.")
        self._append_force_log(f"نسخه موتور حذف: NexaClean Pro 3.5 | Administrator: {'بله' if is_windows_admin() else 'خیر'}")
        self._append_force_log(f"هویت اجرا: {windows_identity() or 'نامشخص'}")
        if self.force_log_file:
            self._append_force_log(f"فایل گزارش کامل: {self.force_log_file}")
        self.force_delete_thread = threading.Thread(
            target=self._force_delete_worker,
            args=(folder, self.force_takeown_var.get(), self.force_clear_attrs_var.get(), self.force_reboot_var.get()),
            daemon=True,
            name="force-folder-delete"
        )
        self.force_delete_thread.start()

    def cancel_force_delete(self):
        if not self.force_delete_active:
            return
        self.force_delete_stop_event.set()
        proc = self.force_active_proc
        if proc is not None:
            self._terminate_force_process_tree(proc)
        self.force_status_var.set("درخواست توقف ثبت شد؛ عملیات جاری در اولین نقطه امن متوقف می‌شود.")
        self._append_force_log("درخواست توقف توسط کاربر.")

    def _terminate_force_process_tree(self, proc):
        if proc is None:
            return
        try:
            if sys.platform.startswith("win"):
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.run(
                    ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=flags, timeout=8
                )
            elif proc.poll() is None:
                proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _run_force_process(self, args, phase_name, timeout_seconds=1800):
        """Run a Windows maintenance command without making the UI look frozen.

        Output goes to a temporary file instead of PIPE so verbose tools cannot deadlock
        when their pipe buffer fills. While the command is running, a heartbeat is sent
        to the UI every few seconds. The operation is cancellable and has a hard timeout.
        """
        self.force_queue.put(("force_log", f"{phase_name} ..."))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
        started = time.monotonic()
        next_heartbeat = started + 4.0
        try:
            with tempfile.TemporaryFile(mode="w+b") as capture:
                proc = subprocess.Popen(
                    args, stdout=capture, stderr=subprocess.STDOUT,
                    creationflags=flags
                )
                self.force_active_proc = proc
                while proc.poll() is None:
                    if self.force_delete_stop_event.is_set():
                        self._terminate_force_process_tree(proc)
                        raise InterruptedError("operation cancelled")
                    now = time.monotonic()
                    elapsed = int(now - started)
                    if timeout_seconds and elapsed >= int(timeout_seconds):
                        self._terminate_force_process_tree(proc)
                        self.force_queue.put((
                            "force_log",
                            f"{phase_name}: زمان مجاز تمام شد ({elapsed} ثانیه). فرایند متوقف شد تا برنامه گیر نکند."
                        ))
                        return 124
                    if now >= next_heartbeat:
                        self.force_queue.put((
                            "force_log",
                            f"{phase_name}: هنوز در حال اجراست — {elapsed} ثانیه گذشته. روی پوشه‌های Windows قدیمی این مرحله ممکن است چند دقیقه طول بکشد."
                        ))
                        next_heartbeat = now + 5.0
                    time.sleep(0.15)

                rc = proc.returncode
                try:
                    capture.flush()
                    capture.seek(0, os.SEEK_END)
                    size = capture.tell()
                    capture.seek(max(0, size - 65536))
                    raw = capture.read()
                    detail = raw.decode("utf-8", errors="replace").strip().replace("\r", " ").replace("\n", " ")[-1400:]
                except Exception:
                    detail = ""

                if rc != 0:
                    self.force_queue.put((
                        "force_log",
                        f"{phase_name}: کد {rc} — {detail or 'بدون جزئیات؛ عملیات با روش جایگزین ادامه پیدا می‌کند.'}"
                    ))
                else:
                    self.force_queue.put(("force_log", f"{phase_name}: انجام شد."))
                return rc
        finally:
            self.force_active_proc = None

    def _force_remove_tree(self, folder, phase_label="حذف", time_budget=None):
        """Delete a tree with live progress, bounded fast-pass time, and error diagnosis.

        Unlike shutil.rmtree, this routine never goes silent for minutes. It walks the
        tree itself, does not follow symlinks/junctions, reports progress periodically,
        and classifies Windows errors so the next escalation step can be chosen
        intelligently.
        """
        target = make_long_windows_path(folder)
        started = time.monotonic()
        last_ui = started
        last_log = started
        errors = []
        stats = {
            "visited_files": 0, "visited_dirs": 0,
            "deleted_files": 0, "deleted_dirs": 0,
            "deleted_bytes": 0, "errors": 0,
            "errors_by_kind": defaultdict(int),
            "reparse_points": 0, "timed_out": False,
        }

        def display_path(path):
            p = str(path)
            if p.startswith("\\\\?\\UNC\\"):
                p = "\\\\" + p[8:]
            elif p.startswith("\\\\?\\"):
                p = p[4:]
            return p

        def remember_error(path, exc):
            kind = self._force_error_kind(exc)
            if kind == "missing":
                return
            stats["errors"] += 1
            stats["errors_by_kind"][kind] += 1
            if len(errors) < 40:
                errors.append((display_path(path), str(exc), kind))

        def maybe_report(current="", force=False):
            nonlocal last_ui, last_log
            now = time.monotonic()
            elapsed = int(now - started)
            total_deleted = stats["deleted_files"] + stats["deleted_dirs"]
            if force or now - last_ui >= 1.25:
                cur = os.path.basename(display_path(current).rstrip("\\/")) if current else ""
                cur_txt = f" • فعلی: {cur[:45]}" if cur else ""
                self.force_queue.put((
                    "force_live_status",
                    f"{phase_label}: حذف {stats['deleted_files']:,} فایل + {stats['deleted_dirs']:,} پوشه "
                    f"• بررسی {stats['visited_files'] + stats['visited_dirs']:,} • خطا {stats['errors']:,} "
                    f"• {human_size(stats['deleted_bytes'])} • {elapsed} ثانیه{cur_txt}"
                ))
                last_ui = now
            if force or now - last_log >= 10.0:
                self.force_queue.put((
                    "force_log",
                    f"{phase_label} در حال پیشرفت — حذف‌شده: {total_deleted:,} مورد "
                    f"({human_size(stats['deleted_bytes'])}) • بررسی‌شده: "
                    f"{stats['visited_files'] + stats['visited_dirs']:,} • خطا: {stats['errors']:,} • زمان: {elapsed}s"
                ))
                last_log = now

        def budget_expired():
            if not time_budget:
                return False
            if time.monotonic() - started < float(time_budget):
                return False
            stats["timed_out"] = True
            return True

        def make_writable(path):
            try:
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            except Exception:
                pass

        def remove_leaf(path, is_dir_link=False, known_size=0):
            if self.force_delete_stop_event.is_set():
                raise InterruptedError("operation cancelled")
            try:
                if is_dir_link:
                    os.rmdir(path)
                    stats["deleted_dirs"] += 1
                else:
                    os.remove(path)
                    stats["deleted_files"] += 1
                    stats["deleted_bytes"] += max(0, int(known_size or 0))
                return True
            except FileNotFoundError:
                return True
            except Exception as first_exc:
                make_writable(path)
                try:
                    if is_dir_link:
                        os.rmdir(path)
                        stats["deleted_dirs"] += 1
                    else:
                        os.remove(path)
                        stats["deleted_files"] += 1
                        stats["deleted_bytes"] += max(0, int(known_size or 0))
                    return True
                except FileNotFoundError:
                    return True
                except Exception as exc:
                    remember_error(path, exc if exc is not None else first_exc)
                    return False

        # Iterative post-order traversal: safe for very deep Windows trees and does not
        # recurse into junctions/symlinks that could escape the selected folder.
        stack = [(target, False)]
        while stack:
            if self.force_delete_stop_event.is_set():
                raise InterruptedError("operation cancelled")
            if budget_expired():
                self.force_queue.put((
                    "force_log",
                    f"{phase_label}: سقف زمانی {int(time_budget)} ثانیه برای مرحله سریع رسید؛ "
                    "مرحله متوقف شد تا موتور هوشمند روش بعدی را انتخاب کند."
                ))
                break

            current, expanded = stack.pop()
            maybe_report(current)

            try:
                is_link = os.path.islink(current)
            except Exception:
                is_link = False
            try:
                is_junction = bool(getattr(os.path, "isjunction", lambda _p: False)(current))
            except Exception:
                is_junction = False

            if current != target and (is_link or is_junction):
                stats["reparse_points"] += 1
                try:
                    dir_link = os.path.isdir(current)
                except Exception:
                    dir_link = True
                remove_leaf(current, is_dir_link=dir_link)
                continue

            if expanded:
                stats["visited_dirs"] += 1
                try:
                    os.rmdir(current)
                    stats["deleted_dirs"] += 1
                except FileNotFoundError:
                    pass
                except Exception as exc:
                    make_writable(current)
                    try:
                        os.rmdir(current)
                        stats["deleted_dirs"] += 1
                    except FileNotFoundError:
                        pass
                    except Exception as exc2:
                        remember_error(current, exc2 if exc2 is not None else exc)
                continue

            try:
                with os.scandir(current) as it:
                    entries = list(it)
            except FileNotFoundError:
                continue
            except NotADirectoryError:
                remove_leaf(current, is_dir_link=False)
                continue
            except Exception as exc:
                remember_error(current, exc)
                continue

            stack.append((current, True))
            for entry in reversed(entries):
                if self.force_delete_stop_event.is_set():
                    raise InterruptedError("operation cancelled")
                ep = entry.path
                try:
                    entry_link = entry.is_symlink()
                except Exception:
                    entry_link = False
                try:
                    entry_junction = bool(getattr(os.path, "isjunction", lambda _p: False)(ep))
                except Exception:
                    entry_junction = False
                if entry_link or entry_junction:
                    stats["reparse_points"] += 1
                    try:
                        dir_link = entry.is_dir(follow_symlinks=False) or entry_junction
                    except Exception:
                        dir_link = entry_junction
                    remove_leaf(ep, is_dir_link=dir_link)
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((ep, False))
                    else:
                        stats["visited_files"] += 1
                        try:
                            size = entry.stat(follow_symlinks=False).st_size
                        except Exception:
                            size = 0
                        remove_leaf(ep, is_dir_link=False, known_size=size)
                except FileNotFoundError:
                    pass
                except Exception as exc:
                    remember_error(ep, exc)

        maybe_report(folder, force=True)
        stats["elapsed"] = round(time.monotonic() - started, 1)
        stats["errors_by_kind"] = dict(stats["errors_by_kind"])
        success = not os.path.exists(folder)
        self.force_last_delete_stats = stats
        self.force_queue.put((
            "force_log",
            f"{phase_label} خلاصه: فایل حذف‌شده {stats['deleted_files']:,} • پوشه حذف‌شده {stats['deleted_dirs']:,} "
            f"• حجم {human_size(stats['deleted_bytes'])} • خطا {stats['errors']:,} • "
            f"Reparse/Junction {stats['reparse_points']:,} • زمان {stats['elapsed']}s"
        ))
        if stats["errors"]:
            self.force_queue.put(("force_log", "تحلیل خودکار مشکل: " + self._force_diagnosis_text(stats)))
        return success, stats["errors"], errors, stats

    def _schedule_remaining_tree_for_reboot(self, folder):
        scheduled = 0
        failed = 0
        visited = 0
        started = time.monotonic()
        last_ui = started
        if not os.path.exists(folder):
            return scheduled, failed

        def progress(current=""):
            nonlocal last_ui
            now = time.monotonic()
            if now - last_ui >= 1.5:
                elapsed = int(now - started)
                name = os.path.basename(str(current).rstrip("\\/"))[:45] if current else ""
                self.force_queue.put((
                    "force_live_status",
                    f"زمان‌بندی Restart: بررسی {visited:,} • ثبت {scheduled:,} • ناموفق {failed:,} • {elapsed}s"
                    + (f" • فعلی: {name}" if name else "")
                ))
                last_ui = now

        try:
            for root, dirs, files in os.walk(folder, topdown=False, followlinks=False):
                if self.force_delete_stop_event.is_set():
                    raise InterruptedError("operation cancelled")
                for name in files:
                    p = os.path.join(root, name)
                    visited += 1
                    if schedule_path_delete_on_reboot(p):
                        scheduled += 1
                    else:
                        failed += 1
                    progress(p)
                for name in dirs:
                    p = os.path.join(root, name)
                    visited += 1
                    if schedule_path_delete_on_reboot(p):
                        scheduled += 1
                    else:
                        failed += 1
                    progress(p)
            visited += 1
            if schedule_path_delete_on_reboot(folder):
                scheduled += 1
            else:
                failed += 1
        except PermissionError as exc:
            self.force_queue.put((
                "force_log",
                f"زمان‌بندی Restart هنگام پیمایش Access Denied گرفت: {exc}. خود پوشه اصلی مستقیماً ثبت می‌شود."
            ))
            visited += 1
            if schedule_path_delete_on_reboot(folder):
                scheduled += 1
            else:
                failed += 1
        elapsed = round(time.monotonic() - started, 1)
        self.force_queue.put((
            "force_log",
            f"زمان‌بندی Restart تمام شد: بررسی {visited:,} • ثبت {scheduled:,} • ناموفق {failed:,} • زمان {elapsed}s"
        ))
        return scheduled, failed

    def _force_delete_worker(self, folder, do_takeown, clear_attrs, schedule_reboot):
        try:
            reason = force_delete_block_reason(folder)
            if reason:
                self.force_queue.put(("force_error", "عملیات متوقف شد: " + reason))
                return
            if self.force_delete_stop_event.is_set():
                self.force_queue.put(("force_cancelled", None))
                return

            # Native Windows tools are surprisingly sensitive to path formatting.
            # Always turn D:/Windows into D:\Windows and strip long-path prefixes.
            native = windows_native_path(folder)
            self.force_queue.put(("force_log", f"مسیر Win32 مورد استفاده: {native}"))
            if not os.path.isdir(folder):
                self.force_queue.put(("force_error", "پوشه قبل از شروع عملیات دیگر وجود ندارد."))
                return

            is_win = sys.platform.startswith("win")
            admin_sid = "*S-1-5-32-544"  # Built-in Administrators; language independent.

            # Phase 1: only repair the selected root first. This is intentionally quick.
            # Older builds immediately ran recursive icacls /T on tens of thousands of
            # Windows files, which looked like a freeze for a long time.
            if is_win and do_takeown:
                self.force_queue.put(("force_status", "مرحله 1/5: آماده‌سازی دسترسی پوشه اصلی ..."))
                rc = self._run_force_process(
                    ["takeown.exe", "/F", native, "/A"],
                    "takeown پوشه اصلی", timeout_seconds=90
                )
                if rc != 0 and os.path.isdir(folder):
                    self.force_queue.put((
                        "force_log",
                        "takeown روی ریشه کامل موفق نشد، اما پوشه وجود دارد؛ حذف اولیه امتحان می‌شود و فقط در صورت نیاز تعمیر بازگشتی اجرا خواهد شد."
                    ))
                self._run_force_process(
                    ["icacls.exe", native, "/grant:r", f"{admin_sid}:F", "/C", "/Q", "/L"],
                    "icacls پوشه اصلی", timeout_seconds=90
                )

            if is_win and clear_attrs and os.path.exists(folder):
                self.force_queue.put(("force_status", "مرحله 2/5: برداشتن Attribute از پوشه اصلی ..."))
                self._run_force_process(
                    ["attrib.exe", "-R", "-S", "-H", native, "/L"],
                    "attrib پوشه اصلی", timeout_seconds=60
                )

            if self.force_delete_stop_event.is_set():
                raise InterruptedError

            # Fast/safe first delete. shutil.rmtree on current Python does not descend
            # through Windows directory junctions, so it is safer than a blind shell rd.
            self.force_queue.put(("force_status", "مرحله 3/5: تلاش حذف مستقیم قبل از پردازش سنگین مجوزها ..."))
            self.force_queue.put(("force_log", "حذف مستقیم اولیه شروع شد؛ اگر مجوزهای فعلی کافی باشند مرحله بازگشتی کاملاً رد می‌شود."))
            success, error_count, errors, first_stats = self._force_remove_tree(
                folder, phase_label="حذف اولیه", time_budget=120
            )
            if success:
                self.force_queue.put(("force_done", (folder, 0, 0)))
                return

            diagnosis = self._force_diagnosis_text(first_stats)
            self.force_queue.put(("force_log", f"حذف اولیه کامل نشد؛ {error_count:,} خطای مؤثر ثبت شد."))
            self.force_queue.put(("force_log", "تصمیم موتور: " + diagnosis))
            for sample in errors[:6]:
                p, err, kind = sample
                self.force_queue.put(("force_log", f"نمونه [{kind}]: {p} — {err}"))

            kinds = first_stats.get("errors_by_kind", {})
            acl_needed = kinds.get("access_denied", 0) > 0 or kinds.get("other", 0) > 0
            locked_only = bool(kinds.get("locked", 0)) and not acl_needed

            if is_win and do_takeown and os.path.exists(folder) and acl_needed:
                self.force_queue.put(("force_status", "مرحله 4/5: تعمیر بازگشتی مالکیت و مجوزها فقط برای خطاهای دسترسی ..."))
                # Use /A to assign ownership to Administrators. /SKIPSL prevents
                # takeown from following symbolic links/reparse links outside the tree.
                take_rc = self._run_force_process(
                    ["takeown.exe", "/F", native, "/A", "/R", "/D", "Y", "/SKIPSL"],
                    "takeown بازگشتی", timeout_seconds=1200
                )
                if take_rc not in (0, 124) and os.path.isdir(folder):
                    self.force_queue.put((
                        "force_log",
                        "takeown بازگشتی برای بعضی موارد خطا داد؛ ادامه می‌دهیم چون icacls و حذف نهایی ممکن است همان فایل‌ها را پاک کنند."
                    ))

                if self.force_delete_stop_event.is_set():
                    raise InterruptedError

                self._run_force_process(
                    ["icacls.exe", native, "/grant:r", f"{admin_sid}:(OI)(CI)F", "/T", "/C", "/Q", "/L"],
                    "icacls بازگشتی Full Control", timeout_seconds=1200
                )
            elif is_win and do_takeown and os.path.exists(folder):
                if locked_only:
                    self.force_queue.put((
                        "force_log",
                        "مرحله 4/5 هوشمندانه رد شد: خطاهای باقی‌مانده از نوع فایلِ در حال استفاده هستند؛ "
                        "تغییر مالکیت/ACL این نوع قفل را باز نمی‌کند و فقط زمان را تلف می‌کند."
                    ))
                else:
                    self.force_queue.put(("force_log", "مرحله 4/5 رد شد: Access Denied مؤثری برای تعمیر بازگشتی تشخیص داده نشد."))

            if is_win and clear_attrs and os.path.exists(folder) and acl_needed:
                wildcard = os.path.join(native, "*")
                self._run_force_process(
                    ["attrib.exe", "-R", "-S", "-H", wildcard, "/S", "/D", "/L"],
                    "attrib بازگشتی", timeout_seconds=900
                )
            elif is_win and clear_attrs and os.path.exists(folder):
                self.force_queue.put((
                    "force_log",
                    "attrib بازگشتی رد شد: حذف اولیه Access Denied قابل‌توجهی نشان نداد؛ پیمایش کامل دوباره فقط سرعت را کم می‌کرد."
                ))

            if self.force_delete_stop_event.is_set():
                raise InterruptedError

            self.force_queue.put(("force_status", "مرحله 5/5: حذف نهایی پوشه و محتویات ..."))
            self.force_queue.put(("force_log", "حذف نهایی شروع شد."))
            success, error_count, errors, final_stats = self._force_remove_tree(
                folder, phase_label="حذف نهایی", time_budget=1800
            )
            if success:
                self.force_queue.put(("force_done", (folder, 0, 0)))
                return

            self.force_queue.put(("force_log", f"حذف نهایی کامل نشد؛ {error_count:,} خطای مؤثر ثبت شد."))
            self.force_queue.put(("force_log", "جمع‌بندی هوشمند: " + self._force_diagnosis_text(final_stats)))
            for sample in errors[:8]:
                p, err, kind = sample
                self.force_queue.put(("force_log", f"ناموفق [{kind}]: {p} — {err}"))

            if schedule_reboot and is_win and os.path.exists(folder):
                self.force_queue.put(("force_status", "برخی فایل‌ها قفل هستند؛ زمان‌بندی حذف بعد از Restart ..."))
                scheduled, failed = self._schedule_remaining_tree_for_reboot(folder)
                self.force_queue.put(("force_done", (folder, scheduled, failed)))
            else:
                self.force_queue.put(("force_error", "پوشه کامل حذف نشد. فایل‌های قفل‌شده یا دارای مجوز خاص باقی مانده‌اند."))
        except InterruptedError:
            self.force_queue.put(("force_cancelled", None))
        except Exception as exc:
            self.force_queue.put(("force_error", str(exc)))

    def _poll_force_queue(self):
        try:
            while True:
                kind, payload = self.force_queue.get_nowait()
                if kind == "force_log":
                    self._append_force_log(payload)
                elif kind == "force_status":
                    self.force_status_var.set(payload)
                    self._append_force_log(payload)
                elif kind == "force_live_status":
                    self.force_status_var.set(payload)
                elif kind == "force_done":
                    folder, scheduled, failed = payload
                    self.force_delete_active = False
                    self.force_progress.stop()
                    self.force_cancel_btn.configure(state="disabled")
                    self.force_confirm_var.set(False)
                    if not os.path.exists(folder):
                        self.force_status_var.set("پوشه با موفقیت کامل حذف شد.")
                        self._append_force_log("پوشه به‌طور کامل حذف شد.")
                        extra = f"\n\nگزارش کامل:\n{self.force_log_file}" if self.force_log_file else ""
                        messagebox.showinfo("انجام شد", "پوشه با موفقیت کامل حذف شد." + extra)
                    elif scheduled:
                        self.force_status_var.set(
                            f"{scheduled:,} مورد برای حذف پس از Restart ثبت شد" + (f" • ناموفق: {failed:,}" if failed else "")
                        )
                        self._append_force_log(f"حذف در Restart: ثبت‌شده {scheduled:,} • ناموفق {failed:,}")
                        messagebox.showinfo(
                            "Restart لازم است",
                            f"بخش قابل حذف پاک شد و {scheduled:,} فایل/پوشه قفل‌شده برای حذف هنگام راه‌اندازی بعدی Windows ثبت شد.\n\nسیستم را Restart کنید."
                        )
                    else:
                        self.force_status_var.set("عملیات تمام شد اما بخشی از پوشه باقی مانده است.")
                    self._refresh_force_delete_button()
                elif kind == "force_error":
                    self.force_delete_active = False
                    self.force_progress.stop()
                    self.force_cancel_btn.configure(state="disabled")
                    self.force_status_var.set("خطا: " + str(payload))
                    self._append_force_log("خطا: " + str(payload))
                    self._refresh_force_delete_button()
                    messagebox.showerror("حذف کامل نشد", str(payload))
                elif kind == "force_cancelled":
                    self.force_delete_active = False
                    self.force_progress.stop()
                    self.force_cancel_btn.configure(state="disabled")
                    self.force_status_var.set("عملیات متوقف شد.")
                    self._append_force_log("عملیات متوقف شد.")
                    self._refresh_force_delete_button()
        except queue.Empty:
            pass
        try:
            self.root.after(80, self._poll_force_queue)
        except Exception:
            pass



def main():
    root = tk.Tk()
    app = DuplicateCleanerApp(root)

    def on_close():
        app.stop_event.set()
        app.image_stop_event.set()
        app.force_delete_stop_event.set()
        try:
            if app.force_active_proc is not None:
                app.force_active_proc.terminate()
        except Exception:
            pass
        try:
            PREVIEW_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        try:
            app.image_thumb_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()