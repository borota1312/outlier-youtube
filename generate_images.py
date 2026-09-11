"""
generate_images.py
===================
Baca prompts.json dari SATU folder channel spesifik (output/[genre]/[channel]/),
lalu kirim tiap prompt ke endpoint text-to-image OmniRoute lokal
(POST /v1/images/generations, format OpenAI-compatible).

Hasil gambar disimpan di:
    output/[genre]/[channel]/image/[urutan_prompt].png

Contoh:
    output/heavy_metal_blues/Dark Men/image/1.png
    output/heavy_metal_blues/Dark Men/image/2.png
    ...
    output/heavy_metal_blues/Dark Men/image/45.png

PENTING soal model:
    Model untuk ANALISIS STYLE (chat completion + vision, contoh:
    antigravity/claude-opus-4-6-thinking) BEDA dengan model untuk
    GENERATE GAMBAR (text-to-image). Endpoint /v1/images/generations
    butuh model image-gen sungguhan, contoh dari dokumentasi OmniRoute:
    openai/gpt-image-2, xai/grok-image, together/FLUX.1, nebius/FLUX,
    fireworks/..., hyperbolic/..., dst — cek model apa yang aktif di
    OmniRoute-mu sendiri (GET /v1/images/generations untuk daftar model).

Cara pakai:
    python generate_images.py
    python generate_images.py --genre heavy_metal_blues --channel "Dark Men"
    python generate_images.py --overwrite
"""

import argparse
import base64
import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# KONFIGURASI — sesuaikan bagian ini
# ============================================================

OMNIROUTE_API_KEY = os.getenv("OMNIROUTE_API_KEY")
OMNIROUTE_BASE_URL = "http://localhost:20128/v1"

# GANTI ke model image-generation yang benar-benar tersedia di OmniRoute-mu.
# Model chat/vision (mis. antigravity/claude-opus-4-6-thinking) TIDAK BISA
# dipakai di sini — itu model teks, bukan model text-to-image.
IMAGE_MODEL = "openrouter/openrouter/auto"

OUTPUT_DIR = "output"  # folder induk tempat semua genre disimpan

GENRE_FOLDER = "shamanic_musik"  # genre yang mau diproses
CHANNEL_FOLDER = "Lyma Eve"  # nama channel spesifik yang mau diproses (harus persis sama dengan nama foldernya)

IMAGE_SIZE = "1792x1024"  # rasio landscape mendekati 16:9, sesuaikan dengan opsi yang didukung model image-mu
OVERWRITE_EXISTING = False  # True = timpa gambar yang sudah ada, False = skip

REQUEST_TIMEOUT = 180

MAX_RETRIES = 4
RETRY_BASE_DELAY = 5.0  # detik, naik 2x tiap percobaan (5s, 10s, 20s, 40s...)
DELAY_BETWEEN_IMAGES = 2.0  # jeda antar-gambar biar tidak membanjiri server


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


def call_omniroute_image_generation(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    size: str,
    max_retries: int = MAX_RETRIES,
    base_delay: float = RETRY_BASE_DELAY,
) -> bytes:
    """Kirim prompt ke /v1/images/generations, kembalikan bytes gambar (PNG/JPEG).
    Menangani 2 kemungkinan format respons: b64_json atau url.
    Otomatis retry kalau server balas 429/500/502/503/504."""

    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
    }

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = base_url.rstrip("/") + "/images/generations"

    RETRYABLE_STATUS = {429, 500, 502, 503, 504}
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT
            )

            if resp.status_code in RETRYABLE_STATUS:
                last_error = f"{resp.status_code} {resp.reason}"
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

            resp.raise_for_status()
            data = resp.json()

            items = data.get("data", [])
            if not items:
                raise RuntimeError(
                    f"Respons tidak berisi data gambar: {json.dumps(data)[:500]}"
                )

            item = items[0]

            if "b64_json" in item and item["b64_json"]:
                return base64.b64decode(item["b64_json"])

            if "url" in item and item["url"]:
                img_resp = requests.get(item["url"], timeout=REQUEST_TIMEOUT)
                img_resp.raise_for_status()
                return img_resp.content

            raise RuntimeError(
                f"Format item data tidak dikenali: {json.dumps(item)[:500]}"
            )

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


def process_channel(
    output_dir: str,
    genre_folder: str,
    channel_folder: str,
    base_url: str,
    api_key: str,
    model: str,
    size: str,
    overwrite: bool,
):
    channel_dir = os.path.join(output_dir, genre_folder, channel_folder)

    if not os.path.isdir(channel_dir):
        print(f"Folder channel tidak ditemukan: {channel_dir}")
        sys.exit(1)

    prompts = load_prompts(channel_dir)
    if not prompts:
        print(f"Tidak ada prompt di prompts.json untuk channel '{channel_folder}'.")
        return

    image_dir = os.path.join(channel_dir, "image")
    os.makedirs(image_dir, exist_ok=True)

    print(
        f"[{genre_folder}/{channel_folder}] {len(prompts)} prompt ditemukan. Model: {model}\n"
    )

    total_done, total_skipped, total_failed = 0, 0, 0

    for item in prompts:
        index = item.get("index")
        prompt_text = item.get("prompt")

        if index is None or not prompt_text:
            print(f"  - SKIP: item prompt tidak valid: {item}")
            total_failed += 1
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
            image_bytes = call_omniroute_image_generation(
                base_url, api_key, model, prompt_text, size
            )
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
        description="Generate gambar dari prompts.json untuk SATU channel spesifik via OmniRoute image generation. "
        "Nilai default diambil dari variabel konfigurasi di atas file ini; "
        "argumen di bawah ini opsional kalau mau override tanpa edit source."
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
    parser.add_argument("--base-url", default=OMNIROUTE_BASE_URL)
    parser.add_argument("--api-key", default=OMNIROUTE_API_KEY)
    parser.add_argument("--model", default=IMAGE_MODEL)
    parser.add_argument("--size", default=IMAGE_SIZE)
    parser.add_argument("--overwrite", action="store_true", default=OVERWRITE_EXISTING)
    args = parser.parse_args()

    if not args.api_key:
        print(
            "OMNIROUTE_API_KEY belum di-set. Jalankan:\n"
            '  export OMNIROUTE_API_KEY="api-key-kamu"\n'
            "atau isi langsung di variabel OMNIROUTE_API_KEY pada file ini."
        )
        sys.exit(1)

    if not args.genre or not args.channel:
        print(
            "GENRE_FOLDER dan CHANNEL_FOLDER wajib diisi (baik lewat variabel di atas file "
            "maupun lewat --genre / --channel), supaya script ini cuma proses 1 channel spesifik."
        )
        sys.exit(1)

    process_channel(
        output_dir=args.output_dir,
        genre_folder=args.genre,
        channel_folder=args.channel,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        size=args.size,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
