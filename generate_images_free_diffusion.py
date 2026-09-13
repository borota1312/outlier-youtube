"""
generate_image_free_diffusion.py
================================
Stable Diffusion Free API Image Generator with Auto-Crop to 16:9

Workflow:
1. Read prompts.json dari channel folder
2. Generate image via Stable Diffusion API (size: 4:3 landscape)
3. Auto-crop to 16:9 (landscape) - center crop vertical
4. Save to image_references/{index}.png

Output disimpan di:
    output/[genre]/[channel]/image_references/[index].png

PERBEDAAN dengan Wombo:
    - Wombo: 9:16 vertical portrait (1024x1792) - untuk mobile
    - Stable Diffusion: 4:3 → 16:9 landscape (1024x768 → 1024x576) - untuk YouTube thumbnail

Repository API: https://github.com/Vincent-the-gamer/free-diffusion

Cara pakai:
    python generate_image_free_diffusion.py --genre afrobeats_affirmations --channel "Vibra Positiva"

    python generate_image_free_diffusion.py --genre afrobeats_affirmations --channel "Vibra Positiva" --overwrite

    python generate_image_free_diffusion.py --genre afrobeats_affirmations --channel "Vibra Positiva" --model flux --overwrite

    python generate_image_free_diffusion.py --genre afrobeats_affirmations --channel "Vibra Positiva" --indices "1,3,5-10"
"""

import argparse
import io
import json
import os
import sys
import time
from typing import Optional

import requests
from PIL import Image

OUTPUT_DIR = "output"
GENRE_FOLDER = "desert_blues_dub"
CHANNEL_FOLDER = "Bayou Gator Dub"

API_BASE_URL = "https://api.stablediffusion3.net"
CANVAS_ID = "-1574096571"

DEFAULT_MODEL = "flux"
DEFAULT_SIZE = "4:3"

AUTO_CROP_TO_16_9 = True

OVERWRITE_EXISTING = False

REQUEST_TIMEOUT = 60

MAX_RETRIES = 4

RETRY_BASE_DELAY = 5.0

DELAY_BETWEEN_IMAGES = 3.0

POLL_INTERVAL = 3.0
POLL_MAX_WAIT = 180.0

DONE_STATES = {"success"}
FAILED_STATES = {"failed", "error"}


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
        }
    )
    return session


def load_prompts(channel_dir: str) -> list:
    prompts_path = os.path.join(channel_dir, "prompts.json")

    if not os.path.isfile(prompts_path):
        raise FileNotFoundError(f"prompts.json tidak ditemukan di {channel_dir}")

    with open(prompts_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("prompts", [])


def parse_indices(indices_str: str) -> set:
    if not indices_str or not indices_str.strip():
        return set()

    indices = set()
    parts = indices_str.split(",")

    for part in parts:
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            try:
                start, end = part.split("-", 1)
                start = int(start.strip())
                end = int(end.strip())
                indices.update(range(start, end + 1))
            except ValueError:
                pass
        else:
            try:
                indices.add(int(part))
            except ValueError:
                pass

    return indices


def crop_to_16_9(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    width, height = img.size

    target_height = int(width * 9 / 16)

    if target_height > height:
        target_height = height

    crop_y = (height - target_height) // 2
    crop_box = (0, crop_y, width, crop_y + target_height)

    cropped = img.crop(crop_box)

    output = io.BytesIO()
    cropped.save(output, format="PNG")

    return output.getvalue()


def refresh_unique_id(session: requests.Session) -> str:
    url = f"{API_BASE_URL}/api/auth/unique-id?canvas={CANVAS_ID}"

    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    unique_id = data.get("data")

    if not unique_id:
        raise ValueError("Failed to get unique ID from API")

    return unique_id


def free_diffusion(
    session: requests.Session,
    prompt: str,
    unique_id: str,
    model: str = DEFAULT_MODEL,
    size: str = DEFAULT_SIZE,
) -> str:
    url = f"{API_BASE_URL}/api/v1/generate/create"

    payload = {
        "prompt": prompt,
        "negativePrompt": "",
        "model": model,
        "size": size,
        "batchSize": 1,
    }

    headers = {"UniqueId": unique_id}

    response = session.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    record_uuid = (data.get("data") or {}).get("recordUuid")

    if not record_uuid:
        raise ValueError("Failed to get recordUuid from API")

    return record_uuid


def check_status(session: requests.Session, record_uuid: str, unique_id: str) -> dict:
    url = f"{API_BASE_URL}/api/v1/generate/record-detail?recordUuid={record_uuid}"
    headers = {"UniqueId": unique_id}

    response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    return data.get("data") or {}


def wait_for_completion(
    session: requests.Session,
    record_uuid: str,
    unique_id: str,
    poll_interval: float = POLL_INTERVAL,
    poll_max_wait: float = POLL_MAX_WAIT,
) -> dict:
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time

        if elapsed > poll_max_wait:
            raise TimeoutError(f"Generation timeout after {poll_max_wait:.0f} seconds")

        try:
            status_data = check_status(session, record_uuid, unique_id)

            pic_state = status_data.get("picState", "").lower()

            if pic_state in DONE_STATES:
                return status_data

            if pic_state in FAILED_STATES:
                fail_code = status_data.get("failCode", "Unknown error")
                raise RuntimeError(f"Generation failed: {fail_code}")

            time.sleep(poll_interval)

        except requests.exceptions.RequestException as e:
            print(f"  ⚠️ Polling error: {e}")
            time.sleep(poll_interval)


def extract_image_url(status_data: dict) -> str:
    pic_url_json = status_data.get("picUrl", "[]")

    try:
        pic_url_list = json.loads(pic_url_json)
    except json.JSONDecodeError:
        raise ValueError("Failed to parse picUrl JSON")

    if not pic_url_list or not isinstance(pic_url_list, list):
        raise ValueError("No image URLs found in response")

    for item in pic_url_list:
        if isinstance(item, dict) and "picUrl" in item:
            delete_flag = item.get("deleteFlag", 0)
            if delete_flag == 0:
                return item["picUrl"]

    raise ValueError("No valid image URL found")


def generate_single_image_with_retry(
    session: requests.Session,
    unique_id: str,
    prompt: str,
    model: str,
    size: str,
    max_retries: int = MAX_RETRIES,
    base_delay: float = RETRY_BASE_DELAY,
) -> bytes:
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            record_uuid = free_diffusion(
                session=session,
                prompt=prompt,
                unique_id=unique_id,
                model=model,
                size=size,
            )

            status_data = wait_for_completion(session, record_uuid, unique_id)

            image_url = extract_image_url(status_data)

            response = session.get(image_url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            image_bytes = response.content

            if AUTO_CROP_TO_16_9:
                cropped_bytes = crop_to_16_9(image_bytes)
                return cropped_bytes

            return image_bytes

        except (
            RuntimeError,
            TimeoutError,
            ValueError,
            AttributeError,
            requests.exceptions.RequestException,
        ) as e:
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
            else:
                raise RuntimeError(
                    f"Gagal setelah {max_retries} percobaan. Error terakhir: {last_error}"
                )

    raise RuntimeError(
        f"Gagal setelah {max_retries} percobaan. Error terakhir: {last_error}"
    )


def process_channel(
    output_dir: str,
    genre_folder: str,
    channel_folder: str,
    model: str,
    size: str,
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

    image_dir = os.path.join(channel_dir, "image_references")
    os.makedirs(image_dir, exist_ok=True)

    session = create_session()

    print(f"🔑 Getting unique ID...")
    try:
        unique_id = refresh_unique_id(session)
        print(f"   Unique ID: {unique_id}\n")
    except Exception as e:
        print(f"❌ Gagal mendapatkan unique ID: {e}")
        sys.exit(1)

    print(
        f"[{genre_folder}/{channel_folder}] "
        f"{len(prompts)} prompt ditemukan. "
        f"Model: {model}, Size: {size} → 16:9 (auto-crop)\n"
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
            image_bytes = generate_single_image_with_retry(
                session=session,
                unique_id=unique_id,
                prompt=prompt_text,
                model=model,
                size=size,
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
        description=(
            "Generate gambar dari prompts.json untuk SATU channel spesifik "
            "menggunakan FREE Stable Diffusion API (stablediffusion3.net). "
            "TIDAK perlu API key! "
            "Output: 4:3 → auto-crop ke 16:9 landscape (untuk YouTube thumbnail)."
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
        choices=["flux", "tamarin", "superAnime", "visiCanvas"],
        default=DEFAULT_MODEL,
        help=f"Model yang digunakan (default: {DEFAULT_MODEL})",
    )

    parser.add_argument(
        "--size",
        choices=["4:3"],
        default=DEFAULT_SIZE,
        help="Ukuran generate (default: 4:3, auto-crop to 16:9 after generation)",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=OVERWRITE_EXISTING,
        help="Timpa gambar yang sudah ada",
    )

    parser.add_argument(
        "--indices",
        type=str,
        default=None,
        help=(
            "Filter index prompt yang mau diproses (opsional). "
            "Contoh: '1,3,5-10' akan proses index 1,3,5,6,7,8,9,10 saja. "
            "Jika tidak diisi, semua prompt diproses."
        ),
    )

    args = parser.parse_args()

    if not args.genre or not args.channel:
        print("❌ GENRE dan CHANNEL wajib diisi.")
        sys.exit(1)

    try:
        process_channel(
            output_dir=args.output_dir,
            genre_folder=args.genre,
            channel_folder=args.channel,
            model=args.model,
            size=args.size,
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
