"""
generate_style.py
==================

Menggabungkan fungsionalitas analyze_style.py dan generate_variations.py
menjadi satu script dengan output tunggal image_style.json.

Script ini:
1. Membaca gambar gabungan thumbnail (grid ~10 thumbnail)
2. Menganalisis STYLE VISUAL (art style, warna, lighting, dll)
3. Menganalisis VARIATIONS (pose, angle, background dari thumbnail asli)
4. Menyimpan hasil gabungan ke image_style.json

Struktur output image_style.json:
{
  "art_style": "...",
  "color_palette": "...",
  "visual_complexity": "...",
  "lighting": "...",
  "typography": "...",
  "composition": "...",
  "variations": {
    "poses": [...],
    "angles": [...],
    "backgrounds": [...]
  }
}

Struktur folder:
output/
  [genre]/
    outlier_*.csv
    [channel]/
      thumbnails_10.jpg
      image_style.json  <-- output dari script ini

Cara pakai:
    python generate_style.py
    python generate_style.py --overwrite
    python generate_style.py --output-dir output --genre smooth_jazz
    python generate_style.py --base-url http://localhost:20128/v1 --model combo-analisis
"""

import argparse
import base64
import csv
import glob
import json
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

OMNIROUTE_API_KEY = os.getenv("OMNIROUTE_API_KEY")
OMNIROUTE_BASE_URL = "http://localhost:20128/v1"
OMNIROUTE_MODEL = "thar/mimo-v2.5:free"
OUTPUT_DIR = "output"
GENRE_FOLDER = "desert_blues_dub"
THUMBNAIL_FILENAME = "thumbnails_10.jpg"
THUMBNAIL_FALLBACK_NAMES = [
    "thumbnails_10.jpg",
    "thumbnail_10.jpg",
    "thumbnail_grid.jpg",
    "thumbnails.jpg",
    "thumbnail.jpg",
]
STYLE_OUTPUT_FILENAME = "image_style.json"
OVERWRITE_EXISTING = False
REQUEST_TIMEOUT = 120

MIN_POSE = 8
MIN_ANGLE = 6
MIN_BACKGROUND = 6


def build_style_prompt(genre_name=""):
    formatted_genre = (
        genre_name.replace("_", " ").title() if genre_name else "Music Channel"
    )

    return f"""
You are analyzing a REFERENCE SHEET (a grid of roughly 10 thumbnails from
the SAME YouTube channel) in order to extract a LOCKED VISUAL STYLE that
will be reused VERBATIM across every generated thumbnail in this batch.

This YouTube channel belongs to the music genre: "{formatted_genre}".

Your task:

1. Look ACROSS all 10 thumbnails in the grid.
2. Identify the recurring VISUAL STYLE choices — the EXACT, REUSABLE
   building blocks that appear in EVERY thumbnail.
3. Build a "style_block" field that is a SINGLE PARAGRAPH combining
   art_style + color_palette + visual_complexity + lighting +
   composition. This paragraph will be pasted UNCHANGED into every
   prompt, so it must fully describe the visual treatment on its own.

Focus on:

- art_style: rendering technique, line work, shading, outlines
- color_palette: dominant colors, accent colors, saturation, temperature
- visual_complexity: density of elements, clutter level
- lighting: key light source, fill, shadow style, mood
- composition: framing, camera POV, layout grid, foreground/background

CRITICAL RULES:

- Use a TELEGRAPHIC, REUSABLE phrasing style — write as if you are
  writing a prompt fragment that will be concatenated over and over.
- Include SPECIFIC NAMED ELEMENTS that appear in every panel
  (example: "magenta lava lamp dead center on dashboard", "three
  floating white skull silhouettes", "wispy cartoon smoke", "dark
  starry sky through windshield").
- Do NOT describe specific poses, specific actions, or narrative
  scene content.
- Do NOT mention text, typography, channel name, or watermarks.

Return ONLY a JSON object with these keys:

{{
  "art_style": "...",
  "color_palette": "...",
  "visual_complexity": "...",
  "lighting": "...",
  "composition": "...",
  "style_block": "..."
}}

CRITICAL CHARACTER LIMITS (STRICTLY ENFORCED):

- art_style: Max 200 characters. Concise but descriptive.
- color_palette: Max 250 characters. Dominant colors + temperature.
- visual_complexity: Max 150 characters.
- lighting: Max 250 characters. Key lighting only.
- composition: Max 200 characters. Framing essentials only.
- style_block: Max 950 characters. Must be a single flowing paragraph
  that combines ALL of the above fields into a self-contained reusable
  prompt fragment. NO pipe characters, NO bullet points — single
  paragraph only.

IMPORTANT:
- Write as if generating a MIDJOURNEY / STABLE DIFFUSION style fragment.
- Every named visual element that appears in the example grid MUST be
  preserved in style_block.
- Stay strictly within character limits.
- Be repetitive-friendly: phrases should concatenate cleanly.

Do NOT include markdown code fences. Do NOT include any text before or
after the JSON object.
"""


VARIATION_PROMPT = f"""
You are analyzing a REFERENCE SHEET containing multiple YouTube
thumbnails from the SAME YouTube channel (a grid/contact sheet of
roughly 10 thumbnails).

Your task: identify the CONTENT VARIATIONS that appear across these
thumbnails — the different POSES, CAMERA ANGLES, and BACKGROUND
ENVIRONMENTS that this channel uses repeatedly.

CRITICAL CONSTRAINT — SINGLE THEME LOCK:

All extracted variations MUST remain inside the SAME visual theme
established by the reference thumbnails. Do NOT introduce variations
that would break the visual consistency of the channel.

That means:

- Poses must only describe the SAME subjects (same characters, same
  outfits style, same items being interacted with) in DIFFERENT body
  positions. Do NOT introduce new characters or change core props.
- Backgrounds must only describe DIFFERENT views of the SAME
  environment type or DIFFERENT states of the SAME setting (e.g.,
  "starry night sky through windshield with full moon" vs "starry
  night sky through windshield with single UFO" — SAME setting type).
- Angles must stay within the SAME camera vocabulary the channel
  uses (e.g., if the channel always uses interior-van shots, do not
  suggest "overhead drone shot of city").

Focus on:

1. POSES / ACTIONS
   - What is the subject DOING in each thumbnail?
   - Describe the pose as a SELF-CONTAINED phrase that includes
     BOTH the subject AND the action.
   - Example: "a young woman in a flowing dress twirling with arms raised"
   - Example: "a man in a tailored suit gripping a vintage microphone"
   - Extract at least {MIN_POSE} distinct poses.
   - Keep the SAME subject(s) across all pose entries.

2. CAMERA ANGLES / FRAMING
   - How is the subject framed? (close-up portrait / medium shot /
     full body / overhead view / low angle / etc.)
   - Extract at least {MIN_ANGLE} distinct angles.
   - Stay within the camera vocabulary already used by the channel.

3. BACKGROUND / ENVIRONMENT
   - Describe the SETTING or ENVIRONMENT variations.
   - Each background MUST be a VARIANT of the same general setting
     type (e.g., all "night sky variations" or all "van interior
     variations" — never mix in unrelated settings).
   - Extract at least {MIN_BACKGROUND} distinct backgrounds.

IMPORTANT:

- Each "pose" entry MUST be SELF-CONTAINED: describe WHO is doing
  the action, not just the action.
- Each "angle" entry describes CAMERA FRAMING or PERSPECTIVE only.
- Each "background" entry describes the SETTING in the SAME general
  environment class as the channel.
- Do NOT introduce new characters, new props, or new settings that
  would change the channel theme.

Return ONLY a JSON object with this structure:

{{
  "poses": [
    "...",
    "..."
  ],
  "angles": [
    "...",
    "..."
  ],
  "backgrounds": [
    "...",
    "..."
  ]
}}

Do NOT include markdown code fences. Do NOT include any text before
or after the JSON object.
"""


def sanitize_channel_name(name):
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", name)
    sanitized = sanitized.strip()
    return sanitized


def find_channel_folder(genre_dir, channel_name_raw):
    sanitized_name = sanitize_channel_name(channel_name_raw)
    channel_path = os.path.join(genre_dir, channel_name_raw)
    if os.path.isdir(channel_path):
        return channel_path
    channel_path = os.path.join(genre_dir, sanitized_name)
    if os.path.isdir(channel_path):
        return channel_path
    try:
        folder_names = os.listdir(genre_dir)
    except OSError:
        return None
    for folder_name in folder_names:
        folder_path = os.path.join(genre_dir, folder_name)
        if not os.path.isdir(folder_path):
            continue
        folder_sanitized = sanitize_channel_name(folder_name)
        if folder_sanitized == sanitized_name:
            return folder_path
    return None


def find_thumbnail_image(channel_folder, thumb_filename):
    primary = os.path.join(channel_folder, thumb_filename)
    if os.path.isfile(primary):
        return primary
    for fallback in THUMBNAIL_FALLBACK_NAMES:
        candidate = os.path.join(channel_folder, fallback)
        if os.path.isfile(candidate):
            return candidate
    return None


def encode_image_base64(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def call_omniroute_vision(base_url, api_key, model, image_base64, prompt_text):
    url = f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                    },
                    {"type": "text", "text": prompt_text},
                ],
            }
        ],
        "stream": True,
    }
    resp = requests.post(
        url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT, stream=True
    )
    print(f"    Status : {resp.status_code}")
    print(f"    Type   : {resp.headers.get('content-type')}")
    resp.raise_for_status()
    chunks = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            break
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        choices = data.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        content = delta.get("content")
        if content:
            chunks.append(content)
    if not chunks:
        raise RuntimeError("OmniRoute tidak mengembalikan content.")
    return "".join(chunks)


def extract_style_json(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Tidak ditemukan object JSON.")
    json_str = text[start : end + 1]
    data = json.loads(json_str)

    # =====================================================
    # VALIDASI & TRUNCATE PANJANG FIELD
    # =====================================================

    MAX_LENGTHS = {
        "art_style": 200,
        "color_palette": 250,
        "visual_complexity": 150,
        "lighting": 250,
        "typography": 150,
        "composition": 200,
    }

    truncated_fields = []

    for field, max_len in MAX_LENGTHS.items():
        value = data.get(field, "")

        if len(value) > max_len:
            truncated = value[:max_len].rsplit(" ", 1)[0].strip()
            data[field] = truncated
            truncated_fields.append(f"{field} ({len(value)}→{len(truncated)})")

    if truncated_fields:
        print(f"    ⚠️ Truncated: {', '.join(truncated_fields)}")

    total_length = sum(len(data.get(f, "")) for f in MAX_LENGTHS.keys())
    print(f"    📏 Style total: {total_length} chars (target: ≤1200)")

    if total_length > 1200:
        print(f"    ⚠️ Warning: Style melebihi target {total_length - 1200} chars")

    return data


def extract_variation_json(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Tidak ditemukan object JSON.")
    json_str = text[start : end + 1]
    data = json.loads(json_str)
    required_keys = {"poses", "angles", "backgrounds"}
    if not required_keys.issubset(data.keys()):
        raise ValueError(f"JSON tidak memiliki key yang dibutuhkan: {required_keys}")
    if len(data.get("poses", [])) < MIN_POSE:
        raise ValueError(f"poses harus minimal {MIN_POSE} entri")
    if len(data.get("angles", [])) < MIN_ANGLE:
        raise ValueError(f"angles harus minimal {MIN_ANGLE} entri")
    if len(data.get("backgrounds", [])) < MIN_BACKGROUND:
        raise ValueError(f"backgrounds harus minimal {MIN_BACKGROUND} entri")
    return data


def analyze_combined(
    image_path,
    base_url,
    api_key,
    model,
    genre_name="",
):
    print("  [1/2] Analyzing style...")
    image_base64 = encode_image_base64(image_path)

    style_prompt = build_style_prompt(genre_name)
    style_text = call_omniroute_vision(
        base_url, api_key, model, image_base64, style_prompt
    )
    style_data = extract_style_json(style_text)

    print("  [2/2] Analyzing variations...")
    variation_text = call_omniroute_vision(
        base_url, api_key, model, image_base64, VARIATION_PROMPT
    )
    variation_data = extract_variation_json(variation_text)

    art_style = style_data.get("art_style", "")
    color_palette = style_data.get("color_palette", "")
    visual_complexity = style_data.get("visual_complexity", "")
    lighting = style_data.get("lighting", "")
    composition = style_data.get("composition", "")

    style_parts = [
        art_style,
        color_palette,
        visual_complexity,
        lighting,
        composition,
    ]
    style_block = " ".join(p.strip() for p in style_parts if p.strip())

    combined = {
        "art_style": art_style,
        "color_palette": color_palette,
        "visual_complexity": visual_complexity,
        "lighting": lighting,
        "composition": composition,
        "style_block": style_block,
        "variations": {
            "poses": variation_data.get("poses", []),
            "angles": variation_data.get("angles", []),
            "backgrounds": variation_data.get("backgrounds", []),
        },
    }

    return combined


def process_all(
    output_dir,
    genre_folder,
    base_url,
    api_key,
    model,
    overwrite,
    thumb_filename,
):
    if not api_key:
        print("Error: OMNIROUTE_API_KEY tidak ditemukan.")
        print()
        print("Isi .env:")
        print("OMNIROUTE_API_KEY=api-key-kamu")
        print()
        print("atau:")
        print('export OMNIROUTE_API_KEY="api-key-kamu"')
        sys.exit(1)

    if genre_folder:
        genre_list = [genre_folder]
    else:
        try:
            all_items = os.listdir(output_dir)
        except OSError:
            print(f"Error: folder {output_dir} tidak ditemukan.")
            sys.exit(1)
        genre_list = [
            item for item in all_items if os.path.isdir(os.path.join(output_dir, item))
        ]

    if not genre_list:
        print("Tidak ada genre untuk diproses.")
        return

    print(f"Genre yang akan diproses: {len(genre_list)}")
    for g in genre_list:
        print(f"  - {g}")
    print()

    total_processed = 0
    total_skipped = 0
    total_failed = 0

    for genre_name in genre_list:
        genre_dir = os.path.join(output_dir, genre_name)
        if not os.path.isdir(genre_dir):
            continue

        print(f"Genre: {genre_name}")
        print("=" * 60)

        csv_pattern = os.path.join(genre_dir, "outlier_*.csv")
        csv_files = glob.glob(csv_pattern)

        if not csv_files:
            print(f"  Tidak ada file outlier_*.csv di {genre_dir}")
            print()
            continue

        csv_file = csv_files[0]
        print(f"  CSV: {os.path.basename(csv_file)}")
        print()

        channels = []
        try:
            with open(csv_file, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    status = row.get("status", "").strip().upper()
                    if status == "PASS":
                        channel_name = row.get("channel", "").strip()
                        if channel_name:
                            channels.append(channel_name)
        except Exception as e:
            print(f"  Error membaca {csv_file}: {e}")
            print()
            continue

        if not channels:
            print(f"  Tidak ada channel dengan status PASS di {csv_file}")
            print()
            continue

        print(f"  Channel PASS: {len(channels)}")
        print()

        for channel_name in channels:
            print(f"  Channel: {channel_name}")

            channel_folder = find_channel_folder(genre_dir, channel_name)
            if not channel_folder:
                print(f"    Folder tidak ditemukan")
                print()
                total_failed += 1
                continue

            thumb_path = find_thumbnail_image(channel_folder, thumb_filename)
            if not thumb_path:
                print(f"    Gambar thumbnail tidak ditemukan")
                print()
                total_failed += 1
                continue

            output_file = os.path.join(channel_folder, STYLE_OUTPUT_FILENAME)

            if os.path.isfile(output_file) and not overwrite:
                print(f"    {STYLE_OUTPUT_FILENAME} sudah ada (skip)")
                print()
                total_skipped += 1
                continue

            try:
                print(f"    Thumbnail: {os.path.basename(thumb_path)}")

                combined_data = analyze_combined(
                    thumb_path,
                    base_url,
                    api_key,
                    model,
                    genre_name=genre_name,
                )

                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(combined_data, f, indent=2, ensure_ascii=False)

                print(f"    ✓ {STYLE_OUTPUT_FILENAME}")
                print()
                total_processed += 1

            except (
                requests.RequestException,
                json.JSONDecodeError,
                ValueError,
            ) as e:
                print(f"    Error analisis: {e}")
                print()
                total_failed += 1

            except Exception as e:
                print(f"    Error: {e}")
                total_failed += 1

        print()

    print("=" * 60)
    print("SELESAI")
    print("=" * 60)
    print(f"Berhasil        : {total_processed}")
    print(f"Dilewati        : {total_skipped}")
    print(f"Gagal          : {total_failed}")


def main():
    parser = argparse.ArgumentParser(
        description="Analisis style + variations dari thumbnail, output ke image_style.json"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=OUTPUT_DIR,
        help=f"Folder output (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--genre",
        type=str,
        default=GENRE_FOLDER,
        help="Nama genre tertentu (kosongkan untuk semua genre)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=OMNIROUTE_BASE_URL,
        help=f"Base URL OmniRoute (default: {OMNIROUTE_BASE_URL})",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=OMNIROUTE_API_KEY,
        help="API key OmniRoute (default: dari .env)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=OMNIROUTE_MODEL,
        help=f"Model OmniRoute (default: {OMNIROUTE_MODEL})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite image_style.json yang sudah ada",
    )
    parser.add_argument(
        "--thumb-name",
        type=str,
        default=THUMBNAIL_FILENAME,
        help=f"Nama file thumbnail (default: {THUMBNAIL_FILENAME})",
    )

    args = parser.parse_args()

    if not args.api_key:
        print("Error: OMNIROUTE_API_KEY tidak ditemukan.")
        print()
        print("Isi .env:")
        print("OMNIROUTE_API_KEY=api-key-kamu")
        print()
        print("atau:")
        print('export OMNIROUTE_API_KEY="api-key-kamu"')
        sys.exit(1)

    process_all(
        output_dir=args.output_dir,
        genre_folder=args.genre,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        overwrite=args.overwrite,
        thumb_filename=args.thumb_name,
    )


if __name__ == "__main__":
    main()
