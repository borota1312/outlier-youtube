"""
generate_musics.py
==================

Membaca prompts_music.json dari folder channel:

    output/[genre]/[channel]/prompts_music.json

Lalu mengirim request pembuatan musik ke endpoint API Boppy.me:

    POST https://boppy.me/api/generate

Melakukan polling status pengerjaan musik ke:

    GET https://boppy.me/api/generate/jobs/{jobId}

Setelah selesai (`status == "done"`), mengunduh file audio MP3
dan menyimpannya di:

    output/[genre]/[channel]/audio/[index].mp3


Cara pakai:

    python generate_musics.py

    python generate_musics.py --genre afrobeats_affirmations

    python generate_musics.py --genre afrobeats_affirmations --channel "Positive Afrobeat"

    python generate_musics.py --genre afrobeats_affirmations --channel "Positive Afrobeat" --indices 1 --overwrite

    python generate_musics.py --genre afrobeats_affirmations --channel "Positive Afrobeat" --indices 1-5 --overwrite
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Optional, Set, Tuple

import requests

# ============================================================
# KONFIGURASI DEFAULT
# ============================================================

CREATE_JOB_URL = "https://boppy.me/api/generate"
POLL_JOB_URL_TEMPLATE = "https://boppy.me/api/generate/jobs/{job_id}"

DEFAULT_COOKIE = (
    "_ga=GA1.1.829812580.1787661233; "
    "_ga_18QQ1LWEX6=GS2.1.s1787762320$o7$g1$t1787762720$j60$l0$h0"
)

DEFAULT_HEADERS = {
    "accept": "*/*",
    "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "content-type": "application/json",
    "origin": "https://boppy.me",
    "priority": "u=1, i",
    "referer": "https://boppy.me/create",
    "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
}

OUTPUT_DIR = "output"
GENRE_FOLDER = "ambient_dub"
CHANNEL_FOLDER = "Moonleaf Audio"
PROMPTS_MUSIC_FILENAME = "prompts_music.json"
AUDIO_SUBDIR = "audio"

OVERWRITE_EXISTING = False
POLL_INTERVAL = 4.0
POLL_TIMEOUT = 300.0
DELAY_BETWEEN_TRACKS = 5.0
REQUEST_TIMEOUT = 60.0
MAX_CREATE_RETRIES = 10


# ============================================================
# SANITIZE FILENAME
# ============================================================


def sanitize_filename(name: str) -> str:
    """
    Membersihkan nama judul agar aman digunakan sebagai nama file di OS.
    """
    if not name:
        return ""
    clean = re.sub(r'[<>:"/\\|?*]', "_", str(name)).strip()
    clean = re.sub(r"\s+", " ", clean)
    return clean.rstrip(". ")


# ============================================================
# PARSE INDICES FILTER
# ============================================================


def parse_indices_string(indices_str: str) -> Set[int]:
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
# API HELPERS
# ============================================================


def get_headers(cookie: str) -> dict:
    headers = dict(DEFAULT_HEADERS)
    headers["cookie"] = cookie
    return headers


def create_music_job(payload: dict, cookie: str) -> str:
    """
    Kirim request POST ke https://boppy.me/api/generate
    Kembalikan jobId.
    """
    headers = get_headers(cookie)

    for attempt in range(1, MAX_CREATE_RETRIES + 1):
        try:
            resp = requests.post(
                CREATE_JOB_URL,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

            if resp.status_code == 429:
                wait_time = min(15.0 * attempt, 60.0)
                print(
                    f"  🚨 HTTP 429 (Rate Limit). Menunggu {wait_time:.0f}s sebelum mencoba lagi ({attempt}/{MAX_CREATE_RETRIES})..."
                )
                time.sleep(wait_time)
                continue

            resp.raise_for_status()
            data = resp.json()

            job_id = data.get("jobId")
            if not job_id:
                raise RuntimeError(f"Response tidak berisi jobId: {json.dumps(data)}")

            return job_id

        except requests.exceptions.RequestException as e:
            if attempt >= MAX_CREATE_RETRIES:
                raise RuntimeError(f"Gagal POST create job: {e}")
            wait_time = min(5.0 * attempt, 30.0)
            time.sleep(wait_time)

    raise RuntimeError("Gagal membuat music generation job setelah retries.")


def poll_music_job(
    job_id: str,
    cookie: str,
    poll_interval: float = POLL_INTERVAL,
    poll_timeout: float = POLL_TIMEOUT,
) -> str:
    """
    Polling status job ke https://boppy.me/api/generate/jobs/{job_id}
    sampai status 'done'. Kembalikan download URL (resultUrl / audioUrl).
    """
    headers = get_headers(cookie)
    poll_url = POLL_JOB_URL_TEMPLATE.format(job_id=job_id)
    start_time = time.time()

    while time.sleep(poll_interval) or True:
        elapsed = time.time() - start_time
        if elapsed > poll_timeout:
            raise TimeoutError(
                f"Polling timeout setelah {poll_timeout:.0f}s untuk job {job_id}"
            )

        try:
            resp = requests.get(
                poll_url,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

            if resp.status_code == 429:
                time.sleep(8.0)
                continue

            resp.raise_for_status()
            data = resp.json()

            status = data.get("status", "unknown").lower()
            progress = data.get("progress", 0)

            print(
                f"\r    ⏳ Polling status: {status} ({progress}%) [{elapsed:.0f}s]   ",
                end="",
                flush=True,
            )

            if status in ("done", "complete", "completed", "success"):
                print()
                result_url = data.get("resultUrl")
                audio_url = data.get("audioUrl")

                if result_url:
                    return result_url
                elif audio_url:
                    if audio_url.startswith("http"):
                        return audio_url
                    return "https://boppy.me" + audio_url
                else:
                    raise RuntimeError(
                        f"Job selesai tetapi tidak ada URL audio: {json.dumps(data)}"
                    )

            if status in ("failed", "error", "cancelled"):
                print()
                raise RuntimeError(f"Job gagal: {json.dumps(data)}")

        except requests.exceptions.RequestException as e:
            # Tetap mencoba polling jika error koneksi sementara
            pass


def download_audio_file(download_url: str, output_path: str, cookie: str):
    """
    Unduh file MP3 dari `download_url` dan simpan ke `output_path`.
    """
    headers = get_headers(cookie)
    resp = requests.get(
        download_url,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
        stream=True,
    )
    resp.raise_for_status()

    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)


# ============================================================
# PROCESS CHANNEL
# ============================================================


def process_channel(
    channel_dir: str,
    channel_name: str,
    genre_name: str,
    overwrite: bool,
    indices_str: Optional[str],
    cookie: str,
    poll_interval: float,
    poll_timeout: float,
):
    prompts_file = os.path.join(channel_dir, PROMPTS_MUSIC_FILENAME)
    if not os.path.isfile(prompts_file):
        print(f"  ⏭️ {PROMPTS_MUSIC_FILENAME} tidak ditemukan di {channel_dir}")
        return

    with open(prompts_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    prompts = data.get("prompts", [])
    if not prompts:
        print(f"  ⏭️ Tidak ada prompt di {PROMPTS_MUSIC_FILENAME}")
        return

    # Filter indices jika ada
    target_indices = parse_indices_string(indices_str) if indices_str else None
    if target_indices:
        prompts = [p for p in prompts if p.get("index") in target_indices]
        if not prompts:
            print(f"  ⏭️ Tidak ada prompt dengan index {sorted(target_indices)}.")
            return

    audio_dir = os.path.join(channel_dir, AUDIO_SUBDIR)
    os.makedirs(audio_dir, exist_ok=True)

    print()
    print("-" * 60)
    print(f"[{genre_name}] {channel_name}")
    print(f"  Prompts : {len(prompts)} item")
    print(f"  Audio   : {audio_dir}")
    print("-" * 60)

    total_done = 0
    total_skipped = 0
    total_failed = 0

    for item in prompts:
        idx = item.get("index")
        payload = item.get("payload")

        if idx is None or not payload:
            # Fallback jika payload belum ada, buat payload otomatis
            if idx is None:
                continue
            payload = {
                "caption": item.get("caption", ""),
                "lyrics": item.get("lyrics", "[instrument]"),
                "model": "AceStep_1_5_XL_Turbo_INT8",
                "duration": item.get("duration", 180),
                "bpm": item.get("bpm", 120),
                "format": "mp3",
            }

        title_raw = item.get("title", "")
        safe_title = sanitize_filename(title_raw)

        if safe_title:
            mp3_filename = f"{safe_title}.mp3"
        else:
            mp3_filename = f"{idx}.mp3"

        mp3_path = os.path.join(audio_dir, mp3_filename)

        if os.path.isfile(mp3_path) and not overwrite:
            print(f'  - [{idx}] SKIP: "{mp3_filename}" sudah ada.')
            total_skipped += 1
            continue

        title_display = f'"{safe_title}" ' if safe_title else ""
        print(
            f"  - [{idx}] {title_display}Generate MP3 (bpm: {payload.get('bpm')}, duration: {payload.get('duration')}s)..."
        )
        print(f"    Caption : {payload.get('caption')[:80]}...")

        try:
            # 1. Create Job
            print("    🚀 POST create job...", end="", flush=True)
            job_id = create_music_job(payload, cookie)
            print(f" OK (Job ID: {job_id})")

            # 2. Poll Job
            download_url = poll_music_job(
                job_id=job_id,
                cookie=cookie,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
            )

            # 3. Download MP3
            print(f"    ⬇️ Downloading MP3...", end="", flush=True)
            download_audio_file(download_url, mp3_path, cookie)
            print(f" OK -> {mp3_filename} ({os.path.getsize(mp3_path):,} bytes)")

            total_done += 1

        except Exception as e:
            print(f"  ❌ GAGAL: {e}")
            total_failed += 1

        time.sleep(DELAY_BETWEEN_TRACKS)

    print()
    print(f"  Ringkasan Channel [{channel_name}]:")
    print(
        f"  Berhasil: {total_done} | Dilewati: {total_skipped} | Gagal: {total_failed}"
    )


# ============================================================
# PROCESS ALL
# ============================================================


def process_all(
    output_dir: str,
    genre_folder: str,
    channel_folder: str,
    overwrite: bool,
    indices_str: Optional[str],
    cookie: str,
    poll_interval: float,
    poll_timeout: float,
):
    if not os.path.isdir(output_dir):
        print(f"❌ Error: folder '{output_dir}' tidak ditemukan.")
        sys.exit(1)

    if genre_folder:
        genre_list = [genre_folder]
    else:
        genre_list = [
            d
            for d in sorted(os.listdir(output_dir))
            if os.path.isdir(os.path.join(output_dir, d))
        ]

    if not genre_list:
        print("Tidak ada folder genre untuk diproses.")
        return

    print("=" * 60)
    print("🚀 BOPPY.ME MUSIC GENERATOR (API Integration)")
    print("=" * 60)
    print(f"Genre Folder   : {genre_folder or 'SEMUA'}")
    print(f"Channel Folder : {channel_folder or 'SEMUA'}")
    if indices_str:
        print(f"Filter Indices : {indices_str}")
    print(f"Overwrite      : {overwrite}")
    print("=" * 60)

    for genre_name in genre_list:
        genre_dir = os.path.join(output_dir, genre_name)
        if not os.path.isdir(genre_dir):
            continue

        if channel_folder:
            channel_list = [channel_folder]
        else:
            channel_list = [
                d
                for d in sorted(os.listdir(genre_dir))
                if os.path.isdir(os.path.join(genre_dir, d))
            ]

        for channel_name in channel_list:
            channel_dir = os.path.join(genre_dir, channel_name)
            if not os.path.isdir(channel_dir):
                print(f"⚠️ Folder channel '{channel_dir}' tidak ditemukan.")
                continue

            process_channel(
                channel_dir=channel_dir,
                channel_name=channel_name,
                genre_name=genre_name,
                overwrite=overwrite,
                indices_str=indices_str,
                cookie=cookie,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
            )

    print()
    print("=" * 60)
    print("SELESAI")
    print("=" * 60)


# ============================================================
# MAIN
# ============================================================


def main():
    parser = argparse.ArgumentParser(
        description="Generate & Download MP3 dari prompts_music.json via Boppy.me API"
    )
    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        help=f"Folder induk output (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--genre",
        default=GENRE_FOLDER,
        help="Nama folder genre (kosongkan untuk semua genre)",
    )
    parser.add_argument(
        "--channel",
        default=CHANNEL_FOLDER,
        help="Nama folder channel spesifik (kosongkan untuk semua channel)",
    )
    parser.add_argument(
        "--indices",
        "--index",
        dest="indices",
        type=str,
        default=None,
        help="Filter urutan/index prompt (contoh: 1 atau 1,3,5 atau 1-5)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=OVERWRITE_EXISTING,
        help="Timpa file .mp3 jika sudah ada",
    )
    parser.add_argument(
        "--cookie",
        default=DEFAULT_COOKIE,
        help="Custom cookie header string untuk Boppy.me API",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=POLL_INTERVAL,
        help=f"Interval polling dalam detik (default: {POLL_INTERVAL}s)",
    )
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=POLL_TIMEOUT,
        help=f"Timeout polling dalam detik (default: {POLL_TIMEOUT}s)",
    )

    args = parser.parse_args()

    process_all(
        output_dir=args.output_dir,
        genre_folder=args.genre,
        channel_folder=args.channel,
        overwrite=args.overwrite,
        indices_str=args.indices,
        cookie=args.cookie,
        poll_interval=args.poll_interval,
        poll_timeout=args.poll_timeout,
    )


if __name__ == "__main__":
    main()
