"""
generate_images.py
===================
Baca prompts.json dari SATU folder channel spesifik (output/[genre]/[channel]/),
lalu generate gambar via Cloudflare Workers AI (FLUX.2 klein 9B, text-to-image).

Endpoint:
    POST https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{model}
    Content-Type: multipart/form-data (field: prompt, width, height, steps)

Hasil gambar disimpan di:
    output/[genre]/[channel]/image/[urutan_prompt].png

Contoh:
    output/heavy_metal_blues/Dark Men/image/1.png
    output/heavy_metal_blues/Dark Men/image/2.png
    ...

UKURAN / 16:9:
    FLUX.2 klein menerima width & height bebas -> default 1280x720 (16:9).
    Model ini membalas JSON {"result": {"image": "<base64 JPEG>"}} yang
    didecode lalu disimpan sebagai .png. steps=4 sudah cukup untuk FLUX.2.

GRATIS:
    Free plan Cloudflare = 10.000 neurons/hari (reset 00:00 UTC).
    Model partner (FLUX.2 klein) mungkin perlu accept Terms di dashboard.

KREDENSIAL diambil dari file .env:
    CLOUDFLARE_ACCOUNT_ID=...
    CLOUDFLARE_API_TOKEN=...

Cara pakai:
    python generate_images.py
    python generate_images.py --genre heavy_metal_blues --channel "Dark Men"
    python generate_images.py --overwrite
    python generate_images.py --indices 1,3,5-8
    python generate_images.py --size 1280x720 --steps 4
"""

import argparse
import base64
import io
import json
import os
import sys
import time
from typing import Optional

import requests
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

# ============================================================
# KONFIGURASI — sesuaikan bagian ini
# ============================================================

CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CF_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")

CF_API_BASE = "https://api.cloudflare.com/client/v4/accounts"

# Model text-to-image Cloudflare Workers AI.
# FLUX.2 klein 9B: paling akurat memahami prompt (banjo, vest, fedora, malam).
# Butuh request multipart/form-data, respons JSON base64 (JPEG) -> didecode.
# Alternatif (kompatibel JSON biasa): @cf/bytedance/stable-diffusion-xl-lightning
IMAGE_MODEL = "@cf/black-forest-labs/flux-2-klein-9b"

OUTPUT_DIR = "output"  # folder induk tempat semua genre disimpan

GENRE_FOLDER = "desert_blues_dub"  # genre yang mau diproses
CHANNEL_FOLDER = "Bayou Gator Dub"  # nama channel spesifik yang mau diproses (harus persis sama dengan nama foldernya)

IMAGE_SIZE = "1280x720"  # 16:9 landscape, FLUX.2 terima resolusi bebas
STEPS = 4  # FLUX.2 klein cukup 4 steps (model distilasi)
OVERWRITE_EXISTING = False  # True = timpa gambar yang sudah ada, False = skip

REQUEST_TIMEOUT = 180

MAX_RETRIES = 4
RETRY_BASE_DELAY = 5.0  # detik, naik 2x tiap percobaan (5s, 10s, 20s, 40s...)
DELAY_BETWEEN_IMAGES = 2.0  # jeda antar-gambar biar tidak membanjiri server


def cf_run_url(model: str) -> str:
    return f"{CF_API_BASE}/{CF_ACCOUNT_ID}/ai/run/{model}"


def parse_size(size: str) -> tuple:
    """'1280x720' -> (1280, 720)"""
    try:
        w, h = size.lower().split("x")
        width, height = int(w), int(h)
        if width < 64 or height < 64:
            raise ValueError
        return width, height
    except ValueError:
        raise ValueError(f"Format --size salah: '{size}'. Pakai contoh: 1280x720")


def parse_indices(indices_str: str) -> set:
    """'1,3,5-8' -> {1,3,5,6,7,8}"""
    selected = set()
    for part in indices_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            selected.update(range(int(lo), int(hi) + 1))
        else:
            selected.add(int(part))
    return selected


def load_prompts(channel_dir: str) -> list:
    prompts_path = os.path.join(channel_dir, "prompts.json")
    if not os.path.isfile(prompts_path):
        raise FileNotFoundError(
            f"prompts.json tidak ditemukan di {channel_dir}. "
            f"Jalankan generate_prompts.py dulu untuk channel ini."
        )
    with open(prompts_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("prompts", [])


def call_cloudflare_image(
    model: str,
    prompt: str,
    width: int,
    height: int,
    steps: int,
    max_retries: int = MAX_RETRIES,
    base_delay: float = RETRY_BASE_DELAY,
) -> bytes:
    """Kirim prompt ke Cloudflare Workers AI /ai/run/{model},
    kembalikan bytes gambar (PNG).

    SDXL membalas PNG binary langsung; beberapa model lain (mis. FLUX)
    membalas JSON {"result": {"image": "<base64>"}} — keduanya ditangani.
    Otomatis retry kalau server balas 429/500/502/503/504."""

    payload = {
        "prompt": prompt,
        "steps": str(steps),
        "width": str(width),
        "height": str(height),
    }

    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
    }

    url = cf_run_url(model)

    # FLUX.2 klein mewajibkan multipart/form-data (bukan JSON/urlencoded).
    files = {k: (None, v) for k, v in payload.items()}

    RETRYABLE_STATUS = {429, 500, 502, 503, 504}
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                url, headers=headers, files=files, timeout=REQUEST_TIMEOUT
            )

            if resp.status_code in RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code} {resp.reason}"
                if attempt < max_retries:
                    delay = base_delay * (2 ** (attempt - 1))
                    print(
                        f"[retry {attempt}/{max_retries}, server sibuk: {last_error}, tunggu {delay:.0f}s] ",
                        end="",
                        flush=True,
                    )
                    time.sleep(delay)
                    continue
                resp.raise_for_status()

            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

            content_type = resp.headers.get("Content-Type", "")

            if "json" in content_type.lower():
                data = resp.json()
                if not data.get("success", False):
                    errs = data.get("errors", [])
                    msg = (
                        "; ".join(e.get("message", str(e)) for e in errs)
                        or json.dumps(data)[:300]
                    )
                    raise RuntimeError(f"Cloudflare error: {msg}")
                img_b64 = (data.get("result") or {}).get("image")
                if not img_b64:
                    raise RuntimeError(
                        f"JSON tanpa field result.image: {json.dumps(data)[:300]}"
                    )
                return base64.b64decode(img_b64)

            return resp.content

        except requests.exceptions.RequestException as e:
            last_error = str(e)
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                print(
                    f"[retry {attempt}/{max_retries}, error: {last_error}, tunggu {delay:.0f}s] ",
                    end="",
                    flush=True,
                )
                time.sleep(delay)
                continue
            raise

    raise RuntimeError(
        f"Gagal setelah {max_retries} percobaan. Error terakhir: {last_error}"
    )


def ensure_png_bytes(image_bytes: bytes) -> bytes:
    """FLUX.2 klein membalas JPEG, SDXL membalas PNG.
    Normalisasi semua ke PNG sungguhan agar ekstensi .png konsisten."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return image_bytes

    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def process_channel(
    output_dir: str,
    genre_folder: str,
    channel_folder: str,
    model: str,
    width: int,
    height: int,
    steps: int,
    overwrite: bool,
    indices_str: Optional[str] = None,
):
    channel_dir = os.path.join(output_dir, genre_folder, channel_folder)

    if not os.path.isdir(channel_dir):
        print(f"❌ Folder channel tidak ditemukan: {channel_dir}")
        sys.exit(1)

    prompts = load_prompts(channel_dir)

    if not prompts:
        print(f"❌ Tidak ada prompt di prompts.json untuk channel '{channel_folder}'.")
        return

    selected_indices = None
    if indices_str:
        selected_indices = parse_indices(indices_str)

    image_dir = os.path.join(channel_dir, "image")
    os.makedirs(image_dir, exist_ok=True)

    print(
        f"[{genre_folder}/{channel_folder}] "
        f"{len(prompts)} prompt ditemukan. "
        f"Model: {model}, Size: {width}x{height} (16:9), Steps: {steps}\n"
    )

    total_done, total_skipped, total_failed = 0, 0, 0

    for item in prompts:
        index = item.get("index")
        prompt_text = item.get("prompt")

        if index is None or not prompt_text:
            print(f"  - SKIP: item prompt tidak valid: {item}")
            total_failed += 1
            continue

        if selected_indices is not None and index not in selected_indices:
            continue

        image_path = os.path.join(image_dir, f"{index}.png")

        if os.path.isfile(image_path) and not overwrite:
            print(
                f"  - [{index}] SKIP: gambar sudah ada (pakai --overwrite untuk timpa)"
            )
            total_skipped += 1
            continue

        print(f"  - [{index}] Generate gambar...", end=" ", flush=True)

        try:
            image_bytes = call_cloudflare_image(
                model=model,
                prompt=prompt_text,
                width=width,
                height=height,
                steps=steps,
            )

            image_bytes = ensure_png_bytes(image_bytes)

            with open(image_path, "wb") as f:
                f.write(image_bytes)

            print("OK")
            total_done += 1

        except Exception as e:
            print(f"GAGAL ({e})")
            total_failed += 1

        time.sleep(DELAY_BETWEEN_IMAGES)

    print("\n=== Ringkasan ===")
    print(f"Berhasil : {total_done}")
    print(f"Dilewati : {total_skipped}")
    print(f"Gagal    : {total_failed}")
    print(f"Tersimpan di: {image_dir}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate gambar dari prompts.json untuk SATU channel spesifik "
            "menggunakan Cloudflare Workers AI (SDXL, gratis). "
            "Output PNG 16:9 (default 1280x720). "
            "Kredensial dari .env: CLOUDFLARE_ACCOUNT_ID & CLOUDFLARE_API_TOKEN."
        )
    )

    parser.add_argument("--output-dir", default=OUTPUT_DIR)

    parser.add_argument(
        "--genre", default=GENRE_FOLDER, required=False, help="Nama folder genre"
    )

    parser.add_argument(
        "--channel",
        default=CHANNEL_FOLDER,
        required=False,
        help="Nama folder channel (persis sesuai nama folder)",
    )

    parser.add_argument(
        "--model",
        default=IMAGE_MODEL,
        help=f"Model Workers AI (default: {IMAGE_MODEL})",
    )

    parser.add_argument(
        "--size",
        default=IMAGE_SIZE,
        help="Ukuran output WxH (default: 1280x720 = 16:9)",
    )

    parser.add_argument(
        "--steps",
        type=int,
        default=STEPS,
        help=f"Jumlah sampling steps (default: {STEPS})",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=OVERWRITE_EXISTING,
        help="Timpa gambar yang sudah ada",
    )

    parser.add_argument(
        "--indices",
        default=None,
        help="Hanya generate prompt tertentu, contoh: 1,3,5-8",
    )

    args = parser.parse_args()

    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        print(
            "CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN belum di-set. "
            'Isi di file .env:\n  CLOUDFLARE_ACCOUNT_ID="..."\n  CLOUDFLARE_API_TOKEN="..."'
        )
        sys.exit(1)

    try:
        width, height = parse_size(args.size)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)

    try:
        process_channel(
            output_dir=args.output_dir,
            genre_folder=args.genre,
            channel_folder=args.channel,
            model=args.model,
            width=width,
            height=height,
            steps=args.steps,
            overwrite=args.overwrite,
            indices_str=args.indices,
        )
    except KeyboardInterrupt:
        print("\n\n⚠️ Dibatalkan oleh user (Ctrl+C)")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
