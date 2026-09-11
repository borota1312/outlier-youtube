"""
generate_prompts.py
====================
Scan folder output/[genre]/[nama_channel]/, baca:

- image_style.json -> style visual + variations (pose, angle, background)

lalu generate hingga N prompt gambar UNIK per channel.

Style dikunci dari style_block di image_style.json,
sementara pose, camera angle, dan background
diambil dari image_style.json["variations"] masing-masing channel.

Hasil disimpan sebagai prompts.json di folder channel yang SAMA.

Struktur folder yang diharapkan:

output/
  smooth_jazz/
    Havana Latin Jazz/
      thumbnail.jpg
      image_style.json
      prompts.json

Contoh image_style.json:

{
  "art_style": "...",
  "color_palette": "...",
  "visual_complexity": "...",
  "lighting": "...",
  "typography": "...",
  "composition": "...",
  "style_block": "...",
  "variations": {
    "poses": ["...", "..."],
    "angles": ["...", "..."],
    "backgrounds": ["...", "..."]
  }
}

Cara pakai:

    python generate_prompts.py

    python generate_prompts.py --genre smooth_jazz --count 45 --overwrite
"""

import argparse
import itertools
import json
import os
import random
import sys

# ============================================================
# KONFIGURASI
# ============================================================

OUTPUT_DIR = "output"

GENRE_FOLDER = "ambient_dub"

PROMPT_COUNT = 15

STYLE_FILENAME = "image_style.json"

OVERWRITE_EXISTING = False

RANDOM_SEED = 42


# ============================================================
# TEMPLATE PROMPT
# ============================================================

PROMPT_TEMPLATE_LANDSCAPE = (
    "{style_block} "
    "Show the subject in {pose}, viewed from {angle}, set in {background}. "
    "Aspect ratio 4:3. "
    "CRITICAL QUALITY: Ensure natural proportions for the subject type. "
    "Absolutely no text, no typography, no letters, no words, no written content "
    "visible anywhere in the image. Clean visual only. No watermark."
)

PROMPT_TEMPLATE_PORTRAIT = (
    "{style_block} "
    "Compose the entire image as if it were a horizontal landscape photograph, "
    "then rotate the whole scene 90 degrees clockwise to fill a vertical 9:16 canvas. "
    "The subject's body lies sideways across the frame, head toward bottom-left "
    "and shoulders extending toward top-right, as if a landscape photo were "
    "rotated onto its side — all elements rotated together as one unified scene. "
    "Show the subject in {pose}, viewed from {angle}, set in {background}. "
    "Aspect ratio 9:16. "
    "CRITICAL QUALITY: Ensure natural proportions for the subject type. "
    "Absolutely no text, no typography, no letters, no words, no written content "
    "visible anywhere in the image. Clean visual only. No watermark."
)


# ============================================================
# SANITASI KATA RAWAN CONTENT MODERATION
# ============================================================

SANITIZE_REPLACEMENTS = {
    "moisture sheen": "subtle sheen",
    "wet or oily surfaces": "reflective surfaces",
    "wet skin": "reflective surface",
    "oily skin": "reflective surface",
    "bare skin": "exposed surface",
    "skin, leather, fabric": "clothing, leather, fabric",
    "visible pores": "fine surface detail",
    "sweat": "sheen",
    "glistening skin": "glistening surface",
}


def sanitize_text(text: str) -> str:
    """
    Ganti frasa-frasa yang rawan trigger
    content moderation model image-gen.
    """

    for risky, safe in SANITIZE_REPLACEMENTS.items():

        idx = text.lower().find(risky.lower())

        while idx != -1:

            text = text[:idx] + safe + text[idx + len(risky) :]

            idx = text.lower().find(risky.lower(), idx + len(safe))

    return text


# ============================================================
# FIND CHANNEL
# ============================================================


def find_channel_dirs(output_dir: str, genre_folder: str):
    """
    Kembalikan list:

        (genre_name, channel_name, channel_dir)

    untuk semua folder channel yang memiliki:

        style.json

    """

    if genre_folder:

        genre_dirs = [os.path.join(output_dir, genre_folder)]

    else:

        genre_dirs = [
            os.path.join(output_dir, d)
            for d in sorted(os.listdir(output_dir))
            if os.path.isdir(os.path.join(output_dir, d))
        ]

    results = []

    for genre_dir in genre_dirs:

        if not os.path.isdir(genre_dir):
            continue

        genre_name = os.path.basename(genre_dir)

        for entry in sorted(os.listdir(genre_dir)):

            channel_dir = os.path.join(genre_dir, entry)

            if not os.path.isdir(channel_dir):
                continue

            style_path = os.path.join(channel_dir, STYLE_FILENAME)

            if os.path.isfile(style_path):
                results.append((genre_name, entry, channel_dir))

    return results


# ============================================================
# LOAD STYLE
# ============================================================


def load_style_block(channel_dir: str) -> str:
    """
    Baca image_style.json dan ambil field style_block.

    EXCLUDE typography untuk menghindari text muncul di generated image.
    """

    style_path = os.path.join(channel_dir, STYLE_FILENAME)

    with open(style_path, "r", encoding="utf-8") as f:

        style_data = json.load(f)

    # Bangun style_block dari field individual (SKIP typography)
    parts = [
        style_data.get("art_style", ""),
        style_data.get("color_palette", ""),
        style_data.get("visual_complexity", ""),
        style_data.get("lighting", ""),
        # typography removed - we want no text in generated images
        style_data.get("composition", ""),
    ]

    style_block = " ".join(p.strip() for p in parts if p.strip())

    if not style_block:

        raise ValueError(f"{STYLE_FILENAME} tidak punya field style yang valid.")

    return style_block.strip()


# ============================================================
# LOAD VARIATIONS
# ============================================================


def load_variations(channel_dir: str) -> dict:
    """
    Baca variations dari image_style.json["variations"].

    Mapping key jamak ke singular:

        poses      -> pose
        angles     -> angle
        backgrounds -> background
    """

    style_path = os.path.join(channel_dir, STYLE_FILENAME)

    if not os.path.isfile(style_path):

        raise FileNotFoundError(f"{STYLE_FILENAME} tidak ditemukan: {style_path}")

    with open(style_path, "r", encoding="utf-8") as f:

        style_data = json.load(f)

    variation_data = style_data.get("variations", {})

    if not variation_data:

        raise ValueError(f"{STYLE_FILENAME} tidak memiliki field 'variations'")

    key_mapping = {
        "poses": "pose",
        "angles": "angle",
        "backgrounds": "background",
    }

    variations = {}

    for src_key, dest_key in key_mapping.items():

        value = variation_data.get(src_key)

        if not isinstance(value, list):

            raise ValueError(
                f"Field '{src_key}' di {STYLE_FILENAME}['variations'] "
                f"harus berupa array/list."
            )

        # Hapus value kosong dan duplikat
        cleaned = []

        for item in value:

            if not isinstance(item, str):
                continue

            item = item.strip()

            if not item:
                continue

            if item not in cleaned:
                cleaned.append(item)

        if not cleaned:

            raise ValueError(
                f"Field '{src_key}' di {STYLE_FILENAME}['variations'] "
                f"tidak memiliki item yang valid."
            )

        variations[dest_key] = cleaned

    return variations


# ============================================================
# GENERATE UNIQUE COMBINATIONS
# ============================================================


def generate_unique_variations(variations: dict, count: int, seed=None):
    """
    Hasilkan `count` kombinasi unik dari:

        pose × angle × background

    """

    if count <= 0:

        raise ValueError("Jumlah prompt harus lebih besar dari 0.")

    if seed is not None:

        random.seed(seed)

    keys = ["pose", "angle", "background"]

    all_combos = list(itertools.product(*[variations[key] for key in keys]))

    total_possible = len(all_combos)

    if count > total_possible:

        raise ValueError(
            f"Diminta {count} kombinasi unik, "
            f"tetapi {STYLE_FILENAME} hanya memungkinkan "
            f"{total_possible} kombinasi unik "
            f"({len(variations['pose'])} pose × "
            f"{len(variations['angle'])} angle × "
            f"{len(variations['background'])} background)."
        )

    random.shuffle(all_combos)

    chosen = all_combos[:count]

    return [dict(zip(keys, combo)) for combo in chosen]


# ============================================================
# BUILD PROMPTS
# ============================================================


def build_prompts(style_block: str, variations: dict, count: int, seed=None):
    """
    Gabungkan style_block dari image_style.json
    dengan variasi dari image_style.json["variations"].
    """

    combos = generate_unique_variations(variations, count, seed=seed)

    prompts = []

    for i, combo in enumerate(combos, 1):

        fmt_args = {
            "style_block": style_block,
            "pose": combo["pose"],
            "angle": combo["angle"],
            "background": combo["background"],
        }

        prompt_landscape = sanitize_text(
            PROMPT_TEMPLATE_LANDSCAPE.format(**fmt_args)
        )
        prompt_portrait = sanitize_text(
            PROMPT_TEMPLATE_PORTRAIT.format(**fmt_args)
        )

        prompts.append(
            {
                "index": i,
                "pose": combo["pose"],
                "angle": combo["angle"],
                "background": combo["background"],
                "prompt": prompt_landscape,
                "prompt_portrait": prompt_portrait,
            }
        )

    return prompts


# ============================================================
# PROCESS SEMUA CHANNEL
# ============================================================


def process_all(output_dir: str, genre_folder: str, count: int, overwrite: bool, seed):

    if not os.path.isdir(output_dir):

        print(f"Folder '{output_dir}' tidak ditemukan.")

        sys.exit(1)

    channel_dirs = find_channel_dirs(output_dir, genre_folder)

    if not channel_dirs:

        print(f"Tidak ada folder channel yang memiliki {STYLE_FILENAME}.")

        print("\nPastikan struktur seperti:")

        print("output/smooth_jazz/Havana Latin Jazz/")

        print(f"├── {STYLE_FILENAME}")

        print("└── thumbnail.jpg")

        return

    total_done = 0
    total_skipped = 0
    total_failed = 0

    current_genre = None

    for genre_name, channel_name, channel_dir in channel_dirs:

        if genre_name != current_genre:

            current_genre = genre_name

            print(f"\n[{genre_name}]")

        prompts_path = os.path.join(channel_dir, "prompts.json")

        # ====================================================
        # SKIP EXISTING
        # ====================================================

        if os.path.isfile(prompts_path) and not overwrite:

            print(
                f"  - [{channel_name}] "
                f"SKIP: prompts.json sudah ada "
                f"(pakai --overwrite untuk timpa)"
            )

            total_skipped += 1

            continue

        print(
            f"  - [{channel_name}] " f"Generate {count} prompt...", end=" ", flush=True
        )

        try:

            # =================================================
            # LOAD STYLE
            # =================================================

            style_block = load_style_block(channel_dir)

            # =================================================
            # LOAD VARIATION.JSON
            # =================================================

            variations = load_variations(channel_dir)

            # =================================================
            # HITUNG JUMLAH KOMBINASI
            # =================================================

            possible_combinations = (
                len(variations["pose"])
                * len(variations["angle"])
                * len(variations["background"])
            )

            # =================================================
            # BUILD PROMPTS
            # =================================================

            prompts = build_prompts(
                style_block=style_block, variations=variations, count=count, seed=seed
            )

            # =================================================
            # OUTPUT
            # =================================================

            output_data = {
                "channel": channel_name,
                "genre": genre_name,
                "style_block": style_block,
                "variation_source": (STYLE_FILENAME),
                "variation_count": {
                    "pose": len(variations["pose"]),
                    "angle": len(variations["angle"]),
                    "background": len(variations["background"]),
                    "possible_combinations": (possible_combinations),
                },
                "prompt_count": len(prompts),
                "prompts": prompts,
            }

            with open(prompts_path, "w", encoding="utf-8") as f:

                json.dump(output_data, f, ensure_ascii=False, indent=2)

            print(
                f"OK "
                f"({len(variations['pose'])} pose × "
                f"{len(variations['angle'])} angle × "
                f"{len(variations['background'])} background "
                f"= {possible_combinations} kombinasi)"
            )

            total_done += 1

        except Exception as e:

            print(f"GAGAL ({e})")

            total_failed += 1

    # ========================================================
    # SUMMARY
    # ========================================================

    print("\n=== Ringkasan ===")

    print(f"Berhasil : {total_done}")

    print(f"Dilewati : {total_skipped}")

    print(f"Gagal    : {total_failed}")


# ============================================================
# MAIN
# ============================================================


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Generate prompt gambar unik berdasarkan "
            f"{STYLE_FILENAME} "
            "di masing-masing folder channel."
        )
    )

    parser.add_argument("--output-dir", default=OUTPUT_DIR, help="Folder induk output.")

    parser.add_argument(
        "--genre",
        default=GENRE_FOLDER,
        help=("Nama folder genre spesifik. " "Kosongkan untuk semua genre."),
    )

    parser.add_argument(
        "--count",
        type=int,
        default=PROMPT_COUNT,
        help=("Jumlah prompt unik per channel."),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=OVERWRITE_EXISTING,
        help=("Timpa prompts.json jika sudah ada."),
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=RANDOM_SEED,
        help=(
            "Seed random. "
            "Gunakan None di source code jika ingin "
            "hasil berbeda setiap run."
        ),
    )

    args = parser.parse_args()

    process_all(
        output_dir=args.output_dir,
        genre_folder=args.genre,
        count=args.count,
        overwrite=args.overwrite,
        seed=args.seed,
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
