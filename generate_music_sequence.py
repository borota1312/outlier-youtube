"""
generate_music_sequence.py
===========================

Membaca file MP3 dari folder audio/ di channel tertentu,
mengacak urutan audio, menghitung durasi berdasarkan range
dari meta_populer.json, dan menyimpan urutan ke JSON.

Output disimpan di:
    output/[genre]/[channel]/urutan.json

TIDAK melakukan penggabungan audio (hanya generate metadata urutan).

Cara pakai:

    python generate_music_sequence.py

    python generate_music_sequence.py --genre afrobeats_affirmations --channel "Vibra Positiva" --count 5

    python generate_music_sequence.py --genre afrobeats_affirmations --channel "Vibra Positiva" --count 10 --seed 1

    python generate_music_sequence.py --genre afrobeats_affirmations --channel "Vibra Positiva" --seed 1 --shuffle-seed 42

    python generate_music_sequence.py --genre afrobeats_affirmations --channel "Vibra Positiva" --seed 1 --overwrite
"""

import argparse
import datetime
import glob
import json
import os
import random
import sys
from typing import Optional, Tuple

from mutagen.mp3 import MP3

# ============================================================
# KONFIGURASI DEFAULT
# ============================================================

OUTPUT_DIR = "output"
GENRE_FOLDER = "ambient_dub"
CHANNEL_FOLDER = "Moonleaf Audio"

AUDIO_SUBDIR = "audio"
META_POPULER_FILENAME = "meta_populer.json"
OUTPUT_FILENAME = "urutan.json"

SEQUENCE_COUNT = 15

OVERWRITE_EXISTING = False


# ============================================================
# HELPER: FORMAT TIME
# ============================================================


def format_time(seconds: float) -> str:
    """
    Convert seconds to M:SS format.
    Example: 125.5 → "2:05"
    """
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}:{secs:02d}"


# ============================================================
# HELPER: GET AUDIO DURATION
# ============================================================


def get_audio_duration_seconds(audio_path: str) -> float:
    """
    Membaca durasi MP3 dalam detik menggunakan mutagen (tanpa ffmpeg).
    """
    try:
        audio = MP3(audio_path)
        return audio.info.length
    except Exception as e:
        print(
            f"  ⚠️ Warning: Cannot read duration of {os.path.basename(audio_path)}: {e}"
        )
        return 0.0


# ============================================================
# HELPER: PARSE ISO 8601 DURATION
# ============================================================


def parse_iso8601_duration(duration_str: str) -> int:
    """
    Parse ISO 8601 duration (e.g., PT1H11M54S) ke detik.

    Format: PT[hours]H[minutes]M[seconds]S
    """
    import re

    pattern = r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
    match = re.match(pattern, duration_str)

    if not match:
        return 0

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)

    return hours * 3600 + minutes * 60 + seconds


# ============================================================
# HELPER: GET DURATION RANGE FROM META_POPULER
# ============================================================


def get_duration_range_from_meta(meta_populer_path: str) -> Tuple[int, int]:
    """
    Membaca durasi terpendek dan terpanjang dari meta_populer.json.
    Parse ISO 8601 duration format (PT1H11M54S).
    Return: (min_seconds, max_seconds)
    """
    if not os.path.isfile(meta_populer_path):
        return (600, 3600)  # Fallback default: 10-60 menit

    try:
        with open(meta_populer_path, "r", encoding="utf-8") as f:
            videos = json.load(f)

        durations = []
        for video in videos:
            duration_str = video.get("contentDetails", {}).get("duration", "")
            if duration_str:
                duration_seconds = parse_iso8601_duration(duration_str)
                if duration_seconds > 0:
                    durations.append(duration_seconds)

        if not durations:
            print("  ⚠️ Warning: No valid durations found in meta_populer.json")
            return (600, 3600)

        return (min(durations), max(durations))

    except Exception as e:
        print(f"  ⚠️ Warning: Error reading meta_populer.json: {e}")
        return (600, 3600)


# ============================================================
# MAIN: GENERATE MUSIC SEQUENCE
# ============================================================


def generate_single_sequence(
    audio_files: list,
    duration_min: int,
    duration_max: int,
    sequence_index: int,
) -> dict:
    """
    Generate satu sequence dari audio files.
    Loop file sampai mencapai target duration.
    Hindari track berulang berurutan.
    """
    target_duration = random.randint(duration_min, duration_max)

    shuffled_files = audio_files.copy()
    random.shuffle(shuffled_files)

    cumulative_duration = 0.0
    tracks = []
    file_index = 0
    last_filename = None

    while cumulative_duration < target_duration:
        if file_index >= len(shuffled_files):
            file_index = 0
            random.shuffle(shuffled_files)

        audio_file = shuffled_files[file_index]
        filename = os.path.basename(audio_file)

        if filename == last_filename and len(shuffled_files) > 1:
            file_index += 1
            continue

        audio_duration_sec = get_audio_duration_seconds(audio_file)

        if audio_duration_sec == 0.0:
            file_index += 1
            continue

        start_sec = cumulative_duration
        end_sec = cumulative_duration + audio_duration_sec

        track_data = {
            "index": len(tracks) + 1,
            "filename": filename,
            "start_seconds": round(start_sec, 2),
            "start_time": format_time(start_sec),
            "duration_seconds": round(audio_duration_sec, 2),
            "end_seconds": round(end_sec, 2),
            "end_time": format_time(end_sec),
        }

        tracks.append(track_data)
        cumulative_duration = end_sec
        last_filename = filename
        file_index += 1

    exceeded_by = max(0, cumulative_duration - target_duration)

    return {
        "index": sequence_index,
        "target_duration_seconds": target_duration,
        "actual_duration_seconds": round(cumulative_duration, 2),
        "duration_formatted": format_time(cumulative_duration),
        "exceeded_target_by_seconds": round(exceeded_by, 2),
        "total_tracks": len(tracks),
        "tracks": tracks,
    }


def generate_music_sequence(
    channel_dir: str,
    channel_name: str,
    genre_name: str,
    count: int,
    duration_min: Optional[int] = None,
    duration_max: Optional[int] = None,
    overwrite: bool = False,
):
    """
    Generate multiple urutan audio acak dan simpan ke JSON.
    """
    print()
    print("=" * 60)
    print("🎵 MUSIC SEQUENCE GENERATOR")
    print("=" * 60)
    print(f"Genre         : {genre_name}")
    print(f"Channel       : {channel_name}")
    print(f"Count         : {count}")
    print("=" * 60)

    # ========================================================
    # 1. PATHS
    # ========================================================

    audio_folder = os.path.join(channel_dir, AUDIO_SUBDIR)
    meta_populer_path = os.path.join(channel_dir, META_POPULER_FILENAME)
    output_path = os.path.join(channel_dir, OUTPUT_FILENAME)

    print(f"Output Path   : {output_path}")
    print("=" * 60)

    # ========================================================
    # 2. CHECK OUTPUT FILE
    # ========================================================

    if os.path.exists(output_path) and not overwrite:
        print(f"⚠️ File {OUTPUT_FILENAME} sudah ada.")
        print(f"   Path: {output_path}")
        print("   Gunakan --overwrite untuk menimpa.")
        return

    # ========================================================
    # 3. SCAN AUDIO FILES
    # ========================================================

    print()
    print("📂 Scanning audio files...")

    if not os.path.isdir(audio_folder):
        print(f"❌ Error: Audio folder tidak ditemukan: {audio_folder}")
        sys.exit(1)

    audio_files = sorted(glob.glob(os.path.join(audio_folder, "*.mp3")))

    if not audio_files:
        print(f"❌ Error: Tidak ada file MP3 di folder {audio_folder}")
        sys.exit(1)

    print(f"   Found: {len(audio_files)} MP3 files in audio/")

    # ========================================================
    # 4. GET DURATION RANGE
    # ========================================================

    print()
    print("📊 Reading meta_populer.json...")

    if duration_min is None or duration_max is None:
        meta_duration_min, meta_duration_max = get_duration_range_from_meta(
            meta_populer_path
        )
        if duration_min is None:
            duration_min = meta_duration_min
        if duration_max is None:
            duration_max = meta_duration_max
        source = "meta_populer.json"
    else:
        source = "manual"

    print(
        f"   Duration range: {duration_min}s - {duration_max}s ({format_time(duration_min)} - {format_time(duration_max)})"
    )

    # ========================================================
    # 5. GENERATE SEQUENCES
    # ========================================================

    print()
    print(f"🎲 Generating {count} sequences...")

    sequences = []
    generated_orders = set()
    max_attempts_per_sequence = 100

    for i in range(1, count + 1):
        print(f"\n   [{i}/{count}] Generating sequence #{i}...")

        sequence = None
        for attempt in range(1, max_attempts_per_sequence + 1):
            candidate_sequence = generate_single_sequence(
                audio_files=audio_files,
                duration_min=duration_min,
                duration_max=duration_max,
                sequence_index=i,
            )

            order_signature = tuple(
                [t["filename"] for t in candidate_sequence["tracks"]]
            )

            if order_signature not in generated_orders:
                generated_orders.add(order_signature)
                sequence = candidate_sequence
                break
            else:
                if attempt < max_attempts_per_sequence:
                    print(
                        f"      ⚠️ Collision detected, retrying... (attempt {attempt + 1}/{max_attempts_per_sequence})"
                    )

        if sequence is None:
            print(
                f"      ❌ Error: Cannot generate unique sequence after {max_attempts_per_sequence} attempts"
            )
            sys.exit(1)

        sequences.append(sequence)

        print(
            f"      ✓ {sequence['total_tracks']} tracks | {sequence['duration_formatted']} | Target: {format_time(sequence['target_duration_seconds'])}"
        )

    # ========================================================
    # 6. BUILD OUTPUT JSON
    # ========================================================

    print()
    print("✅ All Sequences Generated")

    output_data = {
        "channel": channel_name,
        "genre": genre_name,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "duration_range": {
            "min_seconds": duration_min,
            "max_seconds": duration_max,
            "source": source,
        },
        "sequence_count": len(sequences),
        "sequences": sequences,
    }

    # ========================================================
    # 7. SAVE JSON
    # ========================================================

    print()
    print("📄 Saving sequences metadata...")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"   Saved: {output_path}")

    print()
    print("=" * 60)
    print(f"SELESAI - {count} sequences generated")
    print("=" * 60)


# ============================================================
# MAIN
# ============================================================


def main():
    parser = argparse.ArgumentParser(
        description="Generate music sequence (urutan audio acak) tanpa penggabungan audio"
    )

    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        help=f"Folder induk output (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--genre",
        default=GENRE_FOLDER,
        help="Nama folder genre",
    )
    parser.add_argument(
        "--channel",
        default=CHANNEL_FOLDER,
        help="Nama folder channel",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=SEQUENCE_COUNT,
        help="Jumlah sequence yang akan di-generate",
    )
    parser.add_argument(
        "--duration-min",
        type=int,
        default=None,
        help="Override durasi minimum dalam detik (optional)",
    )
    parser.add_argument(
        "--duration-max",
        type=int,
        default=None,
        help="Override durasi maksimum dalam detik (optional)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=OVERWRITE_EXISTING,
        help="Timpa file jika sudah ada",
    )

    args = parser.parse_args()

    # ========================================================
    # VALIDATE
    # ========================================================

    if args.count <= 0:
        print("❌ Error: Count harus lebih besar dari 0")
        sys.exit(1)

    if not args.genre or not args.channel:
        print("❌ Error: GENRE dan CHANNEL wajib diisi.")
        sys.exit(1)

    # ========================================================
    # BUILD PATHS
    # ========================================================

    channel_dir = os.path.join(args.output_dir, args.genre, args.channel)

    if not os.path.isdir(channel_dir):
        print(f"❌ Error: Channel folder tidak ditemukan: {channel_dir}")
        sys.exit(1)

    # ========================================================
    # GENERATE
    # ========================================================

    generate_music_sequence(
        channel_dir=channel_dir,
        channel_name=args.channel,
        genre_name=args.genre,
        count=args.count,
        duration_min=args.duration_min,
        duration_max=args.duration_max,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
