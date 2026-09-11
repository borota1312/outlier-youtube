"""
generate_images_wombo_v2.py
===========================
WOMBO Dream AI Image Generator

Behavior:
- 401 / 403 -> refresh Firebase session
- 429        -> langsung reset Firebase session, lalu retry
- Error lain -> retry biasa
"""

import argparse
import json
import os
import sys
import time
import requests
from typing import Tuple

# ============================================================
# KONFIGURASI
# ============================================================

WOMBO_CREATE_URL = "https://api.dream.ai/api/v2/tasks/"
WOMBO_STATUS_URL_TEMPLATE = "https://api.dream.ai/api/v2/tasks/batch?ids={task_id}"

FIREBASE_AUTH_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signUp"

FIREBASE_API_KEY = "AIzaSyDCvp5MTJLUdtBYEKYWXJrlLzu1zuKM6Xw"

STYLE_ID = 163
GEN_TYPE = "NORMAL"
IS_PREMIUM = False

OUTPUT_DIR = "output"
GENRE_FOLDER = "ambient_dub"
CHANNEL_FOLDER = "Moonleaf Audio"

IMAGE_SIZE = "1024x1792"

SIZE_TO_ASPECT_RATIO = {
    # "1792x1024": "ratio_16_9",
    "1024x1792": "ratio_9_16",
    "1024x1024": "ratio_1",
}

OVERWRITE_EXISTING = False

# ============================================================
# ROTASI GAMBAR SETELAH DISIMPAN
# ============================================================

# True  = gambar dirotasi otomatis setelah disimpan.
# False = gambar disimpan apa adanya, tanpa rotasi.
ROTATE_AFTER_SAVE = True

# Derajat rotasi. Positif = counter-clockwise (berlawanan jarum jam),
# negatif = clockwise (searah jarum jam). Ini sesuai konvensi
# Pillow (PIL.Image.rotate).
#
# Contoh:
#   -90  -> putar 90 derajat searah jarum jam
#    90  -> putar 90 derajat berlawanan jarum jam
ROTATE_DEGREES = -90


# ============================================================
# NETWORK CONFIG
# ============================================================

REQUEST_TIMEOUT = 60

# Retry generate image
MAX_RETRIES = 4

# Retry error biasa
RETRY_BASE_DELAY = 5.0

# Jeda antar gambar
DELAY_BETWEEN_IMAGES = 3.0

# Polling
POLL_INTERVAL = 3.0
POLL_MAX_WAIT = 180.0

DONE_STATES = {
    "completed",
    "complete",
    "succeeded",
    "success",
}

FAILED_STATES = {
    "failed",
    "error",
    "cancelled",
    "canceled",
}


# ============================================================
# TOKEN MANAGER
# ============================================================


class TokenManager:

    def __init__(self):
        self.token = None
        self.headers = None

        self.image_count = 0

        self.local_id = None
        self.refresh_token = None

        self.session_number = 0

    # --------------------------------------------------------
    # Firebase Auth
    # --------------------------------------------------------

    def _get_token_from_firebase(self) -> dict:

        print("  🔄 Mengambil token dari Firebase Auth...")

        headers = {
            "accept": "*/*",
            "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "application/json",
            "origin": "https://dream.ai",
            "referer": "https://dream.ai/",
            "sec-ch-ua": (
                '"Not=A?Brand";v="99", '
                '"Google Chrome";v="151", '
                '"Chromium";v="151"'
            ),
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
            "x-browser-channel": "stable",
            "x-browser-copyright": ("Copyright 2026 Google LLC. All Rights Reserved."),
            "x-browser-validation": "ko6WX6RUFF16+y7rLMj9fSJwcBM=",
            "x-browser-year": "2026",
            "x-client-data": "CKmdygEIk6HLAQiGoM0BGKvflDA=",
            "x-client-version": ("Chrome/JsCore/12.10.0/FirebaseCore-web"),
            "x-firebase-gmpid": ("1:181681569359:web:" "277133b57fecf57af0f43a"),
        }

        payload = {"returnSecureToken": True}

        url = f"{FIREBASE_AUTH_URL}?key={FIREBASE_API_KEY}"

        try:

            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=30,
            )

            resp.raise_for_status()

            data = resp.json()

            id_token = data.get("idToken")

            if not id_token:
                raise RuntimeError("Token tidak ditemukan dalam response Firebase")

            print("  ✅ Token berhasil diambil dari Firebase")
            print(f"  📋 Local ID: {data.get('localId')}")
            print(f"  ⏰ Expires in: " f"{data.get('expiresIn')} seconds")

            return {
                "idToken": id_token,
                "localId": data.get("localId"),
                "refreshToken": data.get("refreshToken"),
            }

        except Exception as e:

            print(f"  ❌ Gagal mengambil token: {e}")

            raise

    # --------------------------------------------------------
    # Create new session
    # --------------------------------------------------------

    def get_new_token(self, reason="manual"):

        self.session_number += 1

        print()
        print("=" * 60)
        print("🔄 MEMBANGUN SESSION BARU")
        print(f"   Alasan: {reason}")
        print(f"   Session ke-{self.session_number}")
        print("=" * 60)

        auth_data = self._get_token_from_firebase()

        self.token = auth_data["idToken"]
        self.local_id = auth_data["localId"]
        self.refresh_token = auth_data["refreshToken"]

        # Reset counter gambar untuk session baru
        self.image_count = 0

        self.headers = self._build_headers()

        print("✅ Session baru siap digunakan!")
        print("=" * 60)
        print()

        return self.headers

    # --------------------------------------------------------
    # Build headers
    # --------------------------------------------------------

    def _build_headers(self) -> dict:

        return {
            "accept": "*/*",
            "accept-language": ("id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7"),
            "authorization": f"Bearer {self.token}",
            "content-type": "application/json",
            "origin": "https://dream.ai",
            "priority": "u=1, i",
            "referer": "https://dream.ai/",
            "sec-ch-ua": (
                '"Not=A?Brand";v="99", '
                '"Google Chrome";v="151", '
                '"Chromium";v="151"'
            ),
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "service": "Dream",
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
            "x-app-version": "WEB-7.0.4",
            "x-os-version": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
            "x-service": "Dream",
        }

    # --------------------------------------------------------
    # Get current headers
    # --------------------------------------------------------

    def get_headers(self):

        if not self.token:
            self.get_new_token(reason="initial")

        return self.headers

    # --------------------------------------------------------
    # RESET SESSION KARENA 429
    # --------------------------------------------------------

    def reset_for_rate_limit(self):

        print()
        print("  🚨 HTTP 429 TOO MANY REQUESTS")
        print("  🔄 LANGSUNG RESET TOKEN/SESSION...")

        # Buang session lama
        self.token = None
        self.headers = None
        self.local_id = None
        self.refresh_token = None

        # Buat session Firebase baru
        self.get_new_token(reason="HTTP 429 Too Many Requests")

    # --------------------------------------------------------
    # Catat gambar berhasil
    # --------------------------------------------------------

    def use_token(self):

        self.image_count += 1

    def close(self):
        pass


# ============================================================
# GLOBAL TOKEN MANAGER
# ============================================================

token_manager = None


# ============================================================
# LOAD PROMPTS
# ============================================================


def load_prompts(channel_dir: str) -> list:

    prompts_path = os.path.join(channel_dir, "prompts.json")

    if not os.path.isfile(prompts_path):

        raise FileNotFoundError(f"prompts.json tidak ditemukan " f"di {channel_dir}")

    with open(prompts_path, "r", encoding="utf-8") as f:

        data = json.load(f)

    return data.get("prompts", [])


# ============================================================
# EXTRACT TASK ID
# ============================================================


def extract_task_id(data):

    if isinstance(data, dict):

        for key in (
            "id",
            "task_id",
            "uuid",
        ):

            if key in data and data[key]:
                return data[key]

        for value in data.values():

            if isinstance(value, dict):

                found = extract_task_id(value)

                if found:
                    return found

    elif isinstance(data, list):

        for item in data:

            found = extract_task_id(item)

            if found:
                return found

    return None


# ============================================================
# EXTRACT STATE
# ============================================================


def extract_state(data):

    if isinstance(data, list):

        for item in data:

            found = extract_state(item)

            if found:
                return found

        return None

    if isinstance(data, dict):

        for key in (
            "state",
            "status",
        ):

            if key in data and isinstance(data[key], str):

                return data[key].lower()

        for value in data.values():

            if isinstance(value, (dict, list)):

                found = extract_state(value)

                if found:
                    return found

    return None


# ============================================================
# EXTRACT IMAGE URL
# ============================================================


def extract_image_url(data):

    if isinstance(data, str):

        lower = data.lower()

        if lower.startswith("http") and any(
            ext in lower
            for ext in (
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
            )
        ):

            return data

        return None

    if isinstance(data, dict):

        for key in (
            "photo_url",
            "result_url",
            "final_url",
            "image_url",
            "url",
        ):

            if key in data and isinstance(data[key], str):

                found = extract_image_url(data[key])

                if found:
                    return found

        for value in data.values():

            found = extract_image_url(value)

            if found:
                return found

    elif isinstance(data, list):

        for item in data:

            found = extract_image_url(item)

            if found:
                return found

    return None


# ============================================================
# RETRY AFTER
# ============================================================


def get_retry_after(resp):

    value = resp.headers.get("Retry-After")

    if not value:
        return None

    try:
        return float(value)

    except (TypeError, ValueError):
        return None


# ============================================================
# CREATE WOMBO TASK
# ============================================================


def create_wombo_task(
    prompt: str,
    aspect_ratio: str,
):

    global token_manager

    # =================================================
    # VALIDASI PROMPT
    # =================================================

    # 1. Validasi prompt tidak kosong
    if not prompt or not prompt.strip():
        raise ValueError("Prompt kosong atau hanya whitespace")

    # 2. Validasi panjang minimum
    if len(prompt.strip()) < 50:
        raise ValueError(
            f"Prompt terlalu pendek ({len(prompt)} karakter): {prompt[:100]}"
        )

    # 3. Validasi prompt tidak terpotong (berakhir dengan kata tidak lengkap)
    prompt_lower = prompt.strip().lower()
    incomplete_endings = [
        "in a",
        "in the",
        "with a",
        "with the",
        "from a",
        "from the",
        "of a",
        "of the",
    ]
    for ending in incomplete_endings:
        if prompt_lower.endswith(ending):
            raise ValueError(f"Prompt terpotong: berakhir dengan '{prompt[-30:]}'")

    # 4. Validasi placeholder tidak ter-replace
    if "{" in prompt and "}" in prompt:
        raise ValueError(f"Prompt masih mengandung placeholder: {prompt[:200]}")

    print(f"  ✓ Prompt valid ({len(prompt)} karakter)")

    payload = {
        "is_premium": IS_PREMIUM,
        "input_spec": {
            "prompt": prompt,
            "style": STYLE_ID,
            "aspect_ratio": aspect_ratio,
            "gen_type": GEN_TYPE,
        },
    }

    max_attempts = 3

    for attempt in range(1, max_attempts + 1):

        try:

            headers = token_manager.get_headers()

            # Tambahkan headers yang required oleh API
            headers.update(
                {
                    "service": "Dream",
                    "x-app-version": "WEB-7.0.4",
                    "x-service": "Dream",
                    "origin": "https://dream.ai",
                    "referer": "https://dream.ai/",
                }
            )

            resp = requests.post(
                WOMBO_CREATE_URL,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

            # =================================================
            # 401 / 403
            # =================================================
            # TIDAK DIUBAH:
            # tetap refresh session seperti sebelumnya.
            # =================================================

            if resp.status_code in (
                401,
                403,
            ):

                print(
                    f"  ⚠️ Token expired "
                    f"({resp.status_code}), "
                    "refresh session..."
                )

                token_manager.get_new_token(reason=f"HTTP {resp.status_code}")

                continue

            # =================================================
            # 429
            # =================================================
            # LANGSUNG RESET TOKEN.
            # Tidak menunggu sebelum reset.
            # =================================================

            if resp.status_code == 429:

                print()
                print("  🚨 HTTP 429 TOO MANY REQUESTS")

                # LANGSUNG RESET TOKEN
                token_manager.reset_for_rate_limit()

                # Setelah token baru dibuat,
                # langsung retry tanpa delay.
                print("  ▶️ Retry menggunakan " "token/session baru...")

                continue

            # =================================================
            # 422
            # =================================================

            if resp.status_code == 422:

                print()
                print("=" * 60)
                print("  ❌ HTTP 422 - UNPROCESSABLE ENTITY")
                print("=" * 60)

                print("\n  📤 Payload yang dikirim:")
                print(json.dumps(payload, indent=2, ensure_ascii=False)[:2000])

                print("\n  📥 Response dari API:")
                try:
                    error_detail = resp.json()
                    print(json.dumps(error_detail, indent=2, ensure_ascii=False))
                except Exception:
                    print(resp.text[:1000])

                print(f"\n  🔍 Debugging Info:")
                print(f"     - Prompt length: {len(prompt)} karakter")
                print(f"     - Style ID: {STYLE_ID}")
                print(f"     - Aspect ratio: {aspect_ratio}")
                print(f"     - Gen type: {GEN_TYPE}")
                print(f"     - Is premium: {IS_PREMIUM}")
                print(f"     - Prompt ending: ...{prompt[-100:]}")
                print("=" * 60)

                raise RuntimeError(f"API rejected request (422). See details above.")

            # =================================================
            # Response normal
            # =================================================

            resp.raise_for_status()

            return resp.json()

        except requests.exceptions.RequestException as e:

            if attempt >= max_attempts:
                raise

            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))

            print(f"  ⚠️ Create task error: {e}")

            print(f"  🔁 Retry " f"{attempt}/{max_attempts} " f"dalam {delay:.0f}s...")

            time.sleep(delay)

    raise RuntimeError("Gagal membuat WOMBO task.")


# ============================================================
# GUESS EXTENSION
# ============================================================


def guess_extension(url: str) -> str:

    lower = url.lower()

    for ext in (
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    ):

        if ext in lower:

            if ext == ".jpeg":
                return ".jpg"

            return ext

    return ".jpg"


# ============================================================
# POLL WOMBO TASK
# ============================================================


def poll_wombo_task(
    task_id: str,
) -> Tuple[bytes, str]:

    global token_manager

    status_url = WOMBO_STATUS_URL_TEMPLATE.format(task_id=task_id)

    waited = 0.0

    while waited < POLL_MAX_WAIT:

        try:

            headers = token_manager.get_headers()

            resp = requests.get(
                status_url,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

            # =================================================
            # 401 / 403
            # =================================================

            if resp.status_code in (
                401,
                403,
            ):

                print(
                    f"  ⚠️ Token expired saat "
                    f"polling ({resp.status_code}), "
                    "refresh..."
                )

                token_manager.get_new_token(
                    reason=(f"Polling HTTP " f"{resp.status_code}")
                )

                # Lanjut polling task yang sama
                continue

            # =================================================
            # 429
            # =================================================

            if resp.status_code == 429:

                print()
                print("  🚨 HTTP 429 TOO MANY " "REQUESTS SAAT POLLING")

                # LANGSUNG RESET TOKEN
                token_manager.reset_for_rate_limit()

                # Langsung polling lagi
                print("  ▶️ Lanjut polling " "menggunakan token/session baru...")

                continue

            # =================================================
            # Normal
            # =================================================

            resp.raise_for_status()

            data = resp.json()

            # -------------------------------------------------
            # Image URL
            # -------------------------------------------------

            image_url = extract_image_url(data)

            if image_url:

                img_resp = requests.get(
                    image_url,
                    timeout=REQUEST_TIMEOUT,
                )

                # Jika download image terkena 429,
                # reset token juga.
                if img_resp.status_code == 429:

                    print()
                    print("  🚨 HTTP 429 SAAT " "DOWNLOAD IMAGE")

                    token_manager.reset_for_rate_limit()

                    continue

                img_resp.raise_for_status()

                return (
                    img_resp.content,
                    guess_extension(image_url),
                )

            # -------------------------------------------------
            # State
            # -------------------------------------------------

            state = extract_state(data)

            if state in FAILED_STATES:

                raise RuntimeError("Task gagal. Raw: " + json.dumps(data)[:500])

            if state is None:

                print("\n    [WARN] Struktur " "response tidak dikenali:")

                print(json.dumps(data)[:500])

            time.sleep(POLL_INTERVAL)

            waited += POLL_INTERVAL

        except requests.exceptions.RequestException as e:

            print(f"  ⚠️ Polling error: {e}")

            time.sleep(POLL_INTERVAL)

            waited += POLL_INTERVAL

    raise TimeoutError(
        f"Task {task_id} tidak selesai " f"dalam {POLL_MAX_WAIT:.0f} detik."
    )


# ============================================================
# GENERATE ONE IMAGE
# ============================================================


def generate_one_image(
    prompt: str,
    aspect_ratio: str,
) -> Tuple[bytes, str]:

    global token_manager

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            create_data = create_wombo_task(prompt, aspect_ratio)

            task_id = extract_task_id(create_data)

            if not task_id:

                raise RuntimeError(
                    "Task ID tidak ditemukan. " "Raw: " + json.dumps(create_data)[:500]
                )

            result = poll_wombo_task(task_id)

            # Berhasil
            token_manager.use_token()

            return result

        except requests.exceptions.RequestException as e:

            last_error = str(e)

        except (
            RuntimeError,
            TimeoutError,
        ) as e:

            last_error = str(e)

        # =====================================================
        # Retry generate image
        # =====================================================

        if attempt < MAX_RETRIES:

            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))

            print(
                f"[retry {attempt}/{MAX_RETRIES}, "
                f"error: {last_error}, "
                f"tunggu {delay:.0f}s]"
            )

            time.sleep(delay)

    raise RuntimeError(
        f"Gagal setelah " f"{MAX_RETRIES} percobaan. " f"Error terakhir: {last_error}"
    )


# ============================================================
# PARSE INDICES FILTER
# ============================================================


def parse_indices_string(indices_str: str) -> set:
    """
    Parse string seperti '1', '1,3,5', '1-5', atau '1-3,5,8-10'
    menjadi set integer index.
    """
    if not indices_str:
        return set()

    indices = set()
    parts = str(indices_str).split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                start_str, end_str = part.split("-", 1)
                start = int(start_str.strip())
                end = int(end_str.strip())
                if start <= end:
                    indices.update(range(start, end + 1))
                else:
                    indices.update(range(end, start + 1))
            except ValueError:
                pass
        else:
            try:
                indices.add(int(part))
            except ValueError:
                pass

    return indices


# ============================================================
# ROTASI GAMBAR
# ============================================================


def rotate_image_file(image_path: str, degrees: float):
    """
    Rotasi gambar di `image_path` sebanyak `degrees` derajat,
    lalu timpa file yang sama.

    Konvensi Pillow:
        degrees > 0  -> berlawanan jarum jam (counter-clockwise)
        degrees < 0  -> searah jarum jam (clockwise)

    expand=True supaya kanvas ikut menyesuaikan
    (misal dari 1024x1792 jadi 1792x1024 setelah diputar 90 derajat).
    """

    try:

        from PIL import Image

    except ImportError:

        raise RuntimeError(
            "Pillow belum terinstall. "
            "Jalankan: pip install Pillow --break-system-packages"
        )

    with Image.open(image_path) as img:

        rotated = img.rotate(degrees, expand=True)

        # Simpan dengan format yang sama seperti file asli.
        rotated.save(image_path)


# ============================================================
# PROCESS CHANNEL
# ============================================================


def process_channel(
    output_dir,
    genre_folder,
    channel_folder,
    size,
    overwrite,
    rotate,
    rotate_degrees,
    indices_str=None,
):

    global token_manager

    channel_dir = os.path.join(
        output_dir,
        genre_folder,
        channel_folder,
    )

    if not os.path.isdir(channel_dir):

        print("Folder channel tidak ditemukan: " f"{channel_dir}")

        sys.exit(1)

    prompts = load_prompts(channel_dir)

    if not prompts:

        print(
            "Tidak ada prompt di " "prompts.json untuk channel " f"'{channel_folder}'."
        )

        return

    # ----------------------------------------------------
    # FILTER INDICES
    # ----------------------------------------------------
    target_indices = parse_indices_string(indices_str) if indices_str else None
    if target_indices:
        prompts = [p for p in prompts if p.get("index") in target_indices]
        if not prompts:
            print(
                f"Tidak ada prompt dengan index {sorted(target_indices)} di prompts.json"
            )
            return

    aspect_ratio = SIZE_TO_ASPECT_RATIO.get(size)

    if not aspect_ratio:

        print(f"Ukuran '{size}' tidak ada " "di SIZE_TO_ASPECT_RATIO")

        sys.exit(1)

    image_dir = os.path.join(channel_dir, "image")

    os.makedirs(image_dir, exist_ok=True)

    print("=" * 60)
    print("🚀 WOMBO AUTO GENERATOR " "(Firebase Auth)")
    print("=" * 60)

    print(f"[{genre_folder}/{channel_folder}] " f"{len(prompts)} prompt ditemukan.")

    print(f"Provider: WOMBO dream.ai " f"({aspect_ratio})")

    print("401/403 → refresh token")

    print("429 → LANGSUNG reset token")

    if rotate:

        print(f"🔃 Rotasi otomatis aktif: {rotate_degrees}°")

    print("=" * 60)
    print()

    # ========================================================
    # TOKEN AWAL
    # ========================================================

    token_manager = TokenManager()

    print("🔑 Mengambil token awal...")

    token_manager.get_new_token(reason="initial")

    total_done = 0
    total_skipped = 0
    total_failed = 0

    # ========================================================
    # LOOP PROMPTS
    # ========================================================

    for item in prompts:

        index = item.get("index")

        prompt_text = item.get("prompt_portrait") or item.get("prompt")

        if index is None or not prompt_text:

            print(f"  - SKIP: item prompt " f"tidak valid: {item}")

            total_failed += 1

            continue

        # ----------------------------------------------------
        # Existing
        # ----------------------------------------------------

        existing = [
            p
            for p in (
                f"{index}.jpg",
                f"{index}.jpeg",
                f"{index}.png",
                f"{index}.webp",
            )
            if os.path.isfile(os.path.join(image_dir, p))
        ]

        if existing and not overwrite:

            print(
                f"  - [{index}] SKIP: "
                "gambar sudah ada "
                "(pakai --overwrite "
                "untuk timpa)"
            )

            total_skipped += 1

            continue

        # ----------------------------------------------------
        # Generate
        # ----------------------------------------------------

        print(f"  - [{index}] " "Generate gambar...", end=" ", flush=True)

        try:

            image_bytes, ext = generate_one_image(prompt_text, aspect_ratio)

            image_path = os.path.join(image_dir, f"{index}{ext}")

            # ------------------------------------------------
            # Delete old file
            # ------------------------------------------------

            if overwrite:

                for p in existing:

                    old_path = os.path.join(image_dir, p)

                    if old_path != image_path and os.path.isfile(old_path):

                        os.remove(old_path)

            # ------------------------------------------------
            # Save
            # ------------------------------------------------

            with open(image_path, "wb") as f:

                f.write(image_bytes)

            # ------------------------------------------------
            # Rotate (opsional)
            # ------------------------------------------------

            if rotate:

                try:

                    rotate_image_file(image_path, rotate_degrees)

                    print(f"OK (dirotasi {rotate_degrees}°)")

                except Exception as e:

                    print(f"OK (gagal dirotasi: {e})")

            else:

                print("OK")

            total_done += 1

            print(f"  📊 Gambar ke-" f"{token_manager.image_count} " "dengan token ini")

        except Exception as e:

            print(f"GAGAL ({e})")

            total_failed += 1

        # ----------------------------------------------------
        # Delay antar gambar
        # ----------------------------------------------------

        time.sleep(DELAY_BETWEEN_IMAGES)

    # ========================================================
    # SUMMARY
    # ========================================================

    token_manager.close()

    print()
    print("=== Ringkasan ===")

    print(f"Berhasil : {total_done}")

    print(f"Dilewati : {total_skipped}")

    print(f"Gagal    : {total_failed}")

    print(f"Session dibuat : " f"{token_manager.session_number}")

    print(f"Gambar dengan token terakhir : " f"{token_manager.image_count}")

    print(f"Tersimpan di: {image_dir}")


# ============================================================
# MAIN
# ============================================================


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--output-dir", default=OUTPUT_DIR)

    parser.add_argument("--genre", default=GENRE_FOLDER)

    parser.add_argument("--channel", default=CHANNEL_FOLDER)

    parser.add_argument("--size", default=IMAGE_SIZE)

    parser.add_argument("--overwrite", action="store_true", default=OVERWRITE_EXISTING)

    parser.add_argument(
        "--rotate",
        action="store_true",
        default=ROTATE_AFTER_SAVE,
        help="Rotasi gambar otomatis setelah disimpan.",
    )

    parser.add_argument(
        "--no-rotate",
        dest="rotate",
        action="store_false",
        help="Matikan rotasi otomatis (override --rotate/default).",
    )

    parser.add_argument(
        "--rotate-degrees",
        type=float,
        default=ROTATE_DEGREES,
        help=(
            "Derajat rotasi. Positif = counter-clockwise, "
            "negatif = clockwise. Default: %(default)s"
        ),
    )

    parser.add_argument(
        "--indices",
        "--index",
        dest="indices",
        type=str,
        default=None,
        help="Filter urutan/index prompt tertentu. Contoh: 1 atau 1,3,5 atau 1-5",
    )

    args = parser.parse_args()

    if not args.genre or not args.channel:

        print("GENRE_FOLDER dan " "CHANNEL_FOLDER wajib diisi.")

        sys.exit(1)

    process_channel(
        output_dir=args.output_dir,
        genre_folder=args.genre,
        channel_folder=args.channel,
        size=args.size,
        overwrite=args.overwrite,
        rotate=args.rotate,
        rotate_degrees=args.rotate_degrees,
        indices_str=args.indices,
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
