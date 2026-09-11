"""
generate_video_final.py
========================
Menggabungkan image.png + music.mp3 menjadi video.mp4 menggunakan FFmpeg.

Output: output/[genre]/[channel]/final/[index]/video.mp4

Cara pakai:
    python generate_video_final.py --genre ambient_dub --channel "Moonleaf Audio"

    python generate_video_final.py --genre ambient_dub --channel "Moonleaf Audio" --indices 1

    python generate_video_final.py --genre ambient_dub --channel "Moonleaf Audio" --indices "1,3,5" --overwrite
"""

import argparse
import os
import shutil
import subprocess
import sys
from typing import List, Optional

OUTPUT_DIR = "output"
GENRE_FOLDER = "ambient_dub"
CHANNEL_FOLDER = "Moonleaf Audio"
FINAL_DIR_NAME = "final"
IMAGE_FILENAME = "image.png"
MUSIC_FILENAME = "music.mp3"
VIDEO_FILENAME = "video.mp4"
VIDEO_RESOLUTION = "1280:720"
VIDEO_PRESET = "medium"
VIDEO_CRF = "23"
AUDIO_BITRATE = "192k"


def parse_indices(indices_str: str) -> List[int]:
    indices = set()
    parts = indices_str.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                start, end = part.split("-", 1)
                for i in range(int(start), int(end) + 1):
                    indices.add(i)
            except ValueError:
                raise ValueError(f"Format range index tidak valid: '{part}'")
        else:
            try:
                indices.add(int(part))
            except ValueError:
                raise ValueError(f"Index tidak valid: '{part}'")
    return sorted(list(indices))


def check_ffmpeg() -> tuple:
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    if not ffmpeg_path or not ffprobe_path:
        print("❌ FFmpeg/FFprobe tidak ditemukan di PATH sistem.")
        print("   Pastikan FFmpeg sudah terinstall (misal: brew install ffmpeg).")
        sys.exit(1)
    return ffmpeg_path, ffprobe_path


def get_audio_duration(ffprobe_bin: str, audio_path: str) -> float:
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe error: {result.stderr}")
    return float(result.stdout.strip())


def create_video(
    ffmpeg_bin: str,
    image_path: str,
    audio_path: str,
    output_path: str,
    audio_duration: float,
    resolution: str = VIDEO_RESOLUTION,
    preset: str = VIDEO_PRESET,
    crf: str = VIDEO_CRF,
    audio_bitrate: str = AUDIO_BITRATE,
):
    cmd = [
        ffmpeg_bin,
        "-y",
        "-loop",
        "1",
        "-framerate",
        "1",
        "-i",
        image_path,
        "-i",
        audio_path,
        "-vf",
        f"scale={resolution}:force_original_aspect_ratio=increase,crop={resolution}",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        crf,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-t",
        str(audio_duration),
        "-shortest",
        "-movflags",
        "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg error: {result.stderr.strip()[:200]}")


def find_final_dirs(
    output_dir: str,
    genre_folder: Optional[str] = None,
    channel_folder: Optional[str] = None,
) -> List[str]:
    final_dirs = []

    if not os.path.isdir(output_dir):
        return final_dirs

    if genre_folder:
        genre_path = os.path.join(output_dir, genre_folder)
        if not os.path.isdir(genre_path):
            return final_dirs
        genres = [genre_folder]
    else:
        genres = [
            d
            for d in os.listdir(output_dir)
            if os.path.isdir(os.path.join(output_dir, d))
        ]

    for g in sorted(genres):
        g_path = os.path.join(output_dir, g)

        if channel_folder:
            channels = [channel_folder]
        else:
            channels = [
                c for c in os.listdir(g_path) if os.path.isdir(os.path.join(g_path, c))
            ]

        for c in sorted(channels):
            c_path = os.path.join(g_path, c)
            final_base = os.path.join(c_path, FINAL_DIR_NAME)

            if not os.path.isdir(final_base):
                continue

            for idx_name in sorted(os.listdir(final_base)):
                idx_path = os.path.join(final_base, idx_name)
                if os.path.isdir(idx_path):
                    image_path = os.path.join(idx_path, IMAGE_FILENAME)
                    audio_path = os.path.join(idx_path, MUSIC_FILENAME)
                    if os.path.isfile(image_path) and os.path.isfile(audio_path):
                        final_dirs.append(idx_path)

    return final_dirs


def process_sequence(
    final_seq_dir: str, ffmpeg_bin: str, ffprobe_bin: str, overwrite: bool
) -> tuple:
    image_path = os.path.join(final_seq_dir, IMAGE_FILENAME)
    audio_path = os.path.join(final_seq_dir, MUSIC_FILENAME)
    video_path = os.path.join(final_seq_dir, VIDEO_FILENAME)

    if not os.path.isfile(image_path):
        return "skip", "image.png not found"
    if not os.path.isfile(audio_path):
        return "skip", "music.mp3 not found"

    if os.path.isfile(video_path) and not overwrite:
        return "skip", "video.mp4 already exists"

    try:
        duration = get_audio_duration(ffprobe_bin, audio_path)
        duration_str = f"{int(duration//60)}:{int(duration%60):02d}"
    except Exception as e:
        return "failed", f"Cannot get audio duration: {e}"

    try:
        create_video(ffmpeg_bin, image_path, audio_path, video_path, duration)
        return "success", duration_str
    except Exception as e:
        return "failed", str(e)


def main():
    parser = argparse.ArgumentParser(
        description="Generate video.mp4 from image.png + music.mp3"
    )
    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        help=f"Base directory output (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--genre",
        default=None,
        help="Nama folder genre spesifik di bawah output/ (opsional)",
    )
    parser.add_argument(
        "--channel",
        default=None,
        help="Nama folder channel spesifik (opsional)",
    )
    parser.add_argument(
        "--indices",
        default=None,
        help="Filter index sequence (contoh: 1 atau 1,3,5-10)",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Timpa file video.mp4 jika sudah ada"
    )

    args = parser.parse_args()

    ffmpeg_bin, ffprobe_bin = check_ffmpeg()

    selected_indices = None
    if args.indices:
        try:
            selected_indices = parse_indices(args.indices)
        except ValueError as e:
            print(f"❌ Error argumen --indices: {e}")
            sys.exit(1)

    if args.channel and not args.genre:
        print("❌ Argumen --channel membutuhkan --genre.")
        sys.exit(1)

    final_dirs = find_final_dirs(args.output_dir, args.genre, args.channel)

    if not final_dirs:
        print(
            "❌ Tidak ada folder final/[index] yang memiliki image.png + music.mp3."
        )
        sys.exit(1)

    if selected_indices:
        final_dirs = [
            d
            for d in final_dirs
            if os.path.basename(d).isdigit()
            and int(os.path.basename(d)) in selected_indices
        ]

    if not final_dirs:
        print("❌ Tidak ada folder yang sesuai dengan filter indices.")
        sys.exit(1)

    print(f"🔍 Ditemukan {len(final_dirs)} sequence untuk diproses.\n")

    total_done, total_skipped, total_failed = 0, 0, 0

    for final_seq_dir in final_dirs:
        rel_path = os.path.relpath(final_seq_dir)
        seq_idx = os.path.basename(final_seq_dir)
        print(f"  - [{seq_idx}] Creating video ({rel_path})...", end=" ", flush=True)

        status, msg = process_sequence(final_seq_dir, ffmpeg_bin, ffprobe_bin, args.overwrite)

        if status == "success":
            print(f"OK ({msg})")
            total_done += 1
        elif status == "skip":
            print(f"SKIP ({msg})")
            total_skipped += 1
        else:
            print(f"FAILED ({msg})")
            total_failed += 1

    print("\n=== Ringkasan ===")
    print(f"Berhasil : {total_done}")
    print(f"Dilewati : {total_skipped}")
    print(f"Gagal    : {total_failed}")


if __name__ == "__main__":
    main()
