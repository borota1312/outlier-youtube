"""
analyze_music_dna.py
====================

Menganalisis musical DNA sebuah YouTube channel berdasarkan:

- Metadata channel
- Metadata 5 video populer
- Judul
- Deskripsi
- Tags
- Durasi
- Topic categories

Hanya channel dengan:

    status == PASS

dari:

    output/[genre]/outlier_*.csv

Output HANYA SATU FILE:

    output/[genre]/[channel]/variation_music.json


Contoh struktur:

output/
  afrobeats_affirmations/
    outlier_20260825_120000.csv

    Positive Afrobeat/
      meta_channel.json
      meta_populer.json
      thumbnail.jpg
      variation_music.json


variation_music.json berisi:

{
  "musical_dna": {
    "genre": "...",
    "subgenre": "...",
    "tempo_bpm": "...",
    "rhythm": "...",
    "groove": "...",
    "mood": "...",
    "energy": "...",
    "instrumentation": [
      "...",
      "...",
      "...",
      "...",
      "...",
      "..."
    ]
  },
  "keywords": [
    "...",
    "...",
    "...",
    "...",
    "..."
  ],
  "negative_keywords": [
    "...",
    "...",
    "...",
    "...",
    "..."
  ],
  "variations": {
    "arrangement": [
      "...",
      "...",
      "..."
    ],
    "instrument_focus": [
      "...",
      "...",
      "..."
    ],
    "groove": [
      "...",
      "...",
      "..."
    ],
    "melodic": [
      "...",
      "...",
      "..."
    ],
    "percussion": [
      "...",
      "...",
      "..."
    ],
    "production": [
      "...",
      "...",
      "..."
    ],
    "dynamics": [
      "...",
      "...",
      "..."
    ]
  }
}


Cara pakai:

    python analyze_music_dna.py

    python analyze_music_dna.py --overwrite

    python analyze_music_dna.py \
        --genre afrobeats_affirmations

    python analyze_music_dna.py \
        --base-url http://localhost:20128/v1 \
        --model antigravity/claude-opus-4-6-thinking
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

# ============================================================
# LOAD ENV
# ============================================================

load_dotenv()


# ============================================================
# KONFIGURASI
# ============================================================

OMNIROUTE_API_KEY = os.getenv("OMNIROUTE_API_KEY")

OMNIROUTE_BASE_URL = "http://localhost:20128/v1"

OMNIROUTE_MODEL = "combo-analisis"

OUTPUT_DIR = "output"


# ============================================================
# GENRE
# ============================================================

GENRE_FOLDER = "ambient_dub"


# ============================================================
# OUTPUT
# ============================================================

# HANYA FILE INI YANG DIBUAT
OUTPUT_FILENAME = "variation_music.json"


# ============================================================
# INPUT JSON
# ============================================================

CHANNEL_JSON_FILENAME = "meta_channel.json"

POPULAR_VIDEOS_JSON_FILENAME = "meta_populer.json"


CHANNEL_JSON_FALLBACK_NAMES = [
    "channel.json",
    "channel_metadata.json",
    "channel_meta.json",
    "youtube_channel.json",
    "youtube_channel_metadata.json",
]


POPULAR_VIDEOS_FALLBACK_NAMES = [
    "meta_populer.json",
    "meta_popular.json",
    "populer.json",
    "popular_videos.json",
    "popular_video.json",
    "top_videos.json",
    "top_5_videos.json",
    "popular_videos_5.json",
    "videos.json",
]


# ============================================================
# REQUEST
# ============================================================

REQUEST_TIMEOUT = 120


# ============================================================
# MODEL
# ============================================================

TEMPERATURE = 0.3


# ============================================================
# MUSIC DNA PROMPT
# ============================================================

MUSIC_DNA_PROMPT = """
Analyze the YouTube channel metadata and its most popular videos.

Infer the channel's recurring musical identity for generating NEW
INSTRUMENTAL MUSIC.

Return ONLY valid JSON using EXACTLY this structure:

{
  "musical_dna": {
    "genre": "...",
    "subgenre": "...",
    "bpm_min": 100,
    "bpm_max": 130,
    "tempo_bpm": "100-130",
    "rhythm": "...",
    "groove": "...",
    "mood": "...",
    "energy": "...",
    "instrumentation": ["...", "...", "...", "...", "...", "..."]
  },
  "keywords": ["...", "...", "...", "...", "..."],
  "negative_keywords": ["...", "...", "...", "...", "..."],
  "variations": {
    "arrangement": ["...", "...", "...", "...", "...", "..."],
    "instrument_focus": ["...", "...", "...", "...", "..."],
    "groove": ["...", "...", "...", "...", "..."],
    "melodic": ["...", "...", "...", "...", "..."],
    "percussion": ["...", "...", "...", "...", "..."],
    "production": ["...", "...", "...", "...", "..."],
    "dynamics": ["...", "...", "...", "...", "..."]
  }
}

Rules:

1. Analyze the actual recurring musical characteristics.
2. Do not simply copy words from video titles.
3. Do not invent unrelated genres.
4. The result must describe INSTRUMENTAL MUSIC.
5. Do not use vocals or lyrics as positive musical elements.
6. bpm_min and bpm_max MUST be valid integers (e.g. 100 and 130). tempo_bpm MUST be string range e.g. "100-130".
7. instrumentation MUST contain 5 to 6 concise instrument names (e.g. "Kick drum", "Snare", "Congas", "Electric bass", "Brass", "Synthesizer").
8. keywords MUST contain 5 concise music-generation keywords/tags.
9. negative_keywords MUST contain 5 concise things to avoid (e.g. "vocals", "lyrics", "spoken word").
10. Every variation item in variations MUST be a CONCISE TAG or SHORT PHRASE (maximum 3 to 5 words). Do NOT write long sentences.
11. Every variation must be musically compatible with the detected genre.
12. Variations must be DYNAMIC and derived from this channel's musical DNA.
13. arrangement MUST contain 6 short variation tags.
14. instrument_focus MUST contain 6 short variation tags.
15. groove MUST contain 6 short variation tags.
16. melodic MUST contain 6 short variation tags.
17. percussion MUST contain 6 short variation tags.
18. production MUST contain 6 short variation tags.
19. dynamics MUST contain 6 short variation tags.
20. Avoid vocals, lyrics, singer, chanting, rap, spoken word, and choir.
21. Do not add any fields.
22. Do not remove any fields.
23. No markdown outside code block.
24. JSON only.
"""


# ============================================================
# SANITASI NAMA CHANNEL
# ============================================================


def sanitize_channel_name(name: str) -> str:

    if not name:
        return ""

    name = name.strip()

    invalid_chars = '<>:"/\\|?*'

    for char in invalid_chars:
        name = name.replace(char, "")

    name = " ".join(name.split())

    name = name.rstrip(". ")

    return name


# ============================================================
# FIND CHANNEL DIRECTORY
# ============================================================


def find_channel_dir(
    genre_dir: str,
    channel_name: str,
):

    if not channel_name:
        return None

    original_dir = os.path.join(
        genre_dir,
        channel_name,
    )

    if os.path.isdir(original_dir):
        return original_dir

    sanitized_name = sanitize_channel_name(channel_name)

    if not sanitized_name:
        return None

    sanitized_dir = os.path.join(
        genre_dir,
        sanitized_name,
    )

    if os.path.isdir(sanitized_dir):
        return sanitized_dir

    try:
        folder_names = os.listdir(genre_dir)
    except OSError:
        return None

    for folder_name in folder_names:

        folder_path = os.path.join(
            genre_dir,
            folder_name,
        )

        if not os.path.isdir(folder_path):
            continue

        if sanitize_channel_name(folder_name) == sanitized_name:
            return folder_path

    return None


# ============================================================
# FIND CHANNEL JSON
# ============================================================


def find_json_file(
    channel_dir: str,
    preferred_name: str,
    fallback_names: list,
):

    path = os.path.join(
        channel_dir,
        preferred_name,
    )

    if os.path.isfile(path):
        return path

    for filename in fallback_names:

        path = os.path.join(
            channel_dir,
            filename,
        )

        if os.path.isfile(path):
            return path

    try:
        files = os.listdir(channel_dir)
    except OSError:
        return None

    candidates = []

    for filename in files:

        if not filename.lower().endswith(".json"):
            continue

        lower = filename.lower()

        score = 0

        if "channel" in lower:
            score += 10

        if "meta" in lower:
            score += 5

        path = os.path.join(
            channel_dir,
            filename,
        )

        candidates.append(
            (
                score,
                filename,
                path,
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x[0],
            x[1].lower(),
        ),
        reverse=True,
    )

    return candidates[0][2]


# ============================================================
# FIND POPULAR VIDEOS JSON
# ============================================================


def find_popular_videos_file(
    channel_dir: str,
):
    primary_path = os.path.join(channel_dir, POPULAR_VIDEOS_JSON_FILENAME)
    if os.path.isfile(primary_path):
        return primary_path

    for filename in POPULAR_VIDEOS_FALLBACK_NAMES:

        path = os.path.join(
            channel_dir,
            filename,
        )

        if os.path.isfile(path):
            return path

    try:
        files = os.listdir(channel_dir)
    except OSError:
        return None

    candidates = []

    for filename in files:

        if not filename.lower().endswith(".json"):
            continue

        lower = filename.lower()

        if "channel" in lower and "video" not in lower:
            continue

        if "style" in lower or "variation" in lower or "prompt" in lower:
            continue

        score = 0

        if "popular" in lower or "populer" in lower:
            score += 20

        if "top" in lower:
            score += 15

        if "video" in lower:
            score += 10

        if "5" in lower:
            score += 5

        path = os.path.join(
            channel_dir,
            filename,
        )

        candidates.append(
            (
                score,
                filename,
                path,
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x[0],
            x[1].lower(),
        ),
        reverse=True,
    )

    return candidates[0][2]


# ============================================================
# LOAD JSON
# ============================================================


def load_json_file(path: str):

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as f:

        return json.load(f)


# ============================================================
# FIND OUTLIER CSV
# ============================================================


def find_outlier_csv(
    genre_dir: str,
):

    matches = glob.glob(
        os.path.join(
            genre_dir,
            "outlier_*.csv",
        )
    )

    if not matches:
        return None

    matches.sort(
        key=os.path.getmtime,
        reverse=True,
    )

    return matches[0]


# ============================================================
# GET PASS CHANNELS
# ============================================================


def get_pass_channels(
    csv_path: str,
):

    channels = []

    with open(
        csv_path,
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            status = (row.get("status") or "").strip().upper()

            channel = (row.get("channel") or "").strip()

            if status != "PASS":
                continue

            if not channel:
                continue

            if channel not in channels:
                channels.append(channel)

    return channels


# ============================================================
# BUILD ANALYSIS DATA
# ============================================================


def build_analysis_data(
    channel_data,
    popular_videos_data,
):

    # --------------------------------------------------------
    # CHANNEL
    # --------------------------------------------------------

    if isinstance(channel_data, dict):

        snippet = channel_data.get(
            "snippet",
            {},
        )

        statistics = channel_data.get(
            "statistics",
            {},
        )

        topic_details = channel_data.get(
            "topicDetails",
            {},
        )

        channel_info = {
            "title": snippet.get("title"),
            "description": snippet.get("description"),
            "country": snippet.get("country"),
            "publishedAt": snippet.get("publishedAt"),
            "subscriberCount": statistics.get("subscriberCount"),
            "videoCount": statistics.get("videoCount"),
            "topicCategories": topic_details.get(
                "topicCategories",
                [],
            ),
        }

    else:

        channel_info = channel_data

    # --------------------------------------------------------
    # VIDEOS
    # --------------------------------------------------------

    if isinstance(
        popular_videos_data,
        list,
    ):

        raw_videos = popular_videos_data

    elif isinstance(
        popular_videos_data,
        dict,
    ):

        raw_videos = (
            popular_videos_data.get("items") or popular_videos_data.get("videos") or []
        )

    else:

        raw_videos = []

    videos = []

    for video in raw_videos:

        if not isinstance(video, dict):
            continue

        snippet = video.get(
            "snippet",
            {},
        )

        content_details = video.get(
            "contentDetails",
            {},
        )

        statistics = video.get(
            "statistics",
            {},
        )

        topic_details = video.get(
            "topicDetails",
            {},
        )

        videos.append(
            {
                "id": video.get("id"),
                "publishedAt": snippet.get("publishedAt"),
                "title": snippet.get("title"),
                "description": snippet.get("description"),
                "tags": snippet.get(
                    "tags",
                    [],
                ),
                "defaultLanguage": snippet.get("defaultLanguage"),
                "defaultAudioLanguage": snippet.get("defaultAudioLanguage"),
                "duration": content_details.get("duration"),
                "categoryId": snippet.get("categoryId"),
                "viewCount": statistics.get("viewCount"),
                "likeCount": statistics.get("likeCount"),
                "commentCount": statistics.get("commentCount"),
                "topicCategories": topic_details.get(
                    "topicCategories",
                    [],
                ),
            }
        )

    return {
        "channel": channel_info,
        "popular_videos": videos,
    }


# ============================================================
# BUILD PROMPT
# ============================================================


def build_model_prompt(
    channel_name: str,
    analysis_data: dict,
):

    data_json = json.dumps(
        analysis_data,
        ensure_ascii=False,
        indent=2,
    )

    return (
        MUSIC_DNA_PROMPT
        + "\n\nCHANNEL NAME:\n"
        + channel_name
        + "\n\nSOURCE DATA:\n"
        + data_json
    )


# ============================================================
# PARSE JSON
# ============================================================


def parse_music_json(
    raw_text: str,
):
    text = raw_text.strip()

    # 1. Hapus tag <think>...</think> jika ada
    if "<think>" in text:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # 2. Hapus markdown code blocks ```json ... ``` jika ada
    if "```" in text:
        matches = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if matches:
            text = matches[0].strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        preview = raw_text[:200] + "..." if len(raw_text) > 200 else raw_text
        raise ValueError(f"Response tidak mengandung JSON. Raw output: {preview}")

    json_str = text[start : end + 1]

    # Hapus trailing commas yang sering merusak json parser
    json_str_clean = re.sub(r",\s*([}\]])", r"\1", json_str)

    try:
        return json.loads(json_str_clean)
    except json.JSONDecodeError:
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            preview = json_str[:200] + "..." if len(json_str) > 200 else json_str
            raise ValueError(f"Gagal parse JSON ({e}). Content: {preview}")


# ============================================================
# VALIDATE JSON
# ============================================================


def validate_music_json(
    data: dict,
):

    if not isinstance(data, dict):
        raise ValueError("Output bukan object JSON.")

    required_root = [
        "musical_dna",
        "keywords",
        "negative_keywords",
        "variations",
    ]

    for key in required_root:

        if key not in data:

            raise ValueError(f"Field '{key}' tidak ditemukan.")

    dna = data["musical_dna"]

    required_dna = [
        "genre",
        "subgenre",
        "tempo_bpm",
        "rhythm",
        "groove",
        "mood",
        "energy",
        "instrumentation",
    ]

    for key in required_dna:

        if key not in dna:

            raise ValueError(f"Field musical_dna.{key} tidak ditemukan.")

    if not isinstance(
        dna["instrumentation"],
        list,
    ):

        raise ValueError("instrumentation harus array.")

    if len(dna["instrumentation"]) < 1:

        raise ValueError("instrumentation tidak boleh kosong.")

    if not isinstance(
        data["keywords"],
        list,
    ):

        raise ValueError("keywords harus array.")

    if len(data["keywords"]) < 1:

        raise ValueError("keywords tidak boleh kosong.")

    if not isinstance(
        data["negative_keywords"],
        list,
    ):

        raise ValueError("negative_keywords harus array.")

    if len(data["negative_keywords"]) < 1:

        raise ValueError("negative_keywords tidak boleh kosong.")

    variations = data["variations"]

    variation_keys = [
        "arrangement",
        "instrument_focus",
        "groove",
        "melodic",
        "percussion",
        "production",
        "dynamics",
    ]

    for key in variation_keys:

        if key not in variations:

            raise ValueError(f"variations.{key} tidak ditemukan.")

        if not isinstance(
            variations[key],
            list,
        ):

            raise ValueError(f"variations.{key} harus array.")

        if len(variations[key]) < 1:

            raise ValueError(f"variations.{key} tidak boleh kosong.")

        for item in variations[key]:

            if not isinstance(item, str):
                raise ValueError(f"variations.{key} mengandung item non-string.")

            if not item.strip():
                raise ValueError(f"variations.{key} mengandung item kosong.")

    return True


# ============================================================
# CALL OMNIROUTE
# ============================================================


def call_omniroute(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
):

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": TEMPERATURE,
        "stream": True,
    }

    headers = {
        "Content-Type": "application/json",
    }

    if api_key:

        headers["Authorization"] = f"Bearer {api_key}"

    url = base_url.rstrip("/") + "/chat/completions"

    print()
    print(f"    URL   : {url}")
    print(f"    Model : {model}")
    print("    Mode  : streaming")
    print()

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT,
        stream=True,
    )

    print(f"    Status: {response.status_code}")

    response.raise_for_status()

    chunks = []

    for line in response.iter_lines(decode_unicode=True):

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

        choices = data.get(
            "choices",
            [],
        )

        if not choices:
            continue

        delta = choices[0].get(
            "delta",
            {},
        )

        content = delta.get("content")

        if content:
            chunks.append(content)

    result = "".join(chunks).strip()

    if not result:

        raise RuntimeError("OmniRoute tidak mengembalikan content.")

    return result


# ============================================================
# PROCESS CHANNEL
# ============================================================


def process_channel(
    genre_name: str,
    channel_name: str,
    channel_dir: str,
    base_url: str,
    api_key: str,
    model: str,
    overwrite: bool,
):

    print()
    print("-" * 80)
    print(f"[{genre_name}] {channel_name}")
    print(f"  Folder: {channel_dir}")

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    output_path = os.path.join(
        channel_dir,
        OUTPUT_FILENAME,
    )

    if os.path.isfile(output_path) and not overwrite:

        print("  ⏭️ variation_music.json " "sudah ada.")

        print("     Gunakan --overwrite " "untuk generate ulang.")

        return "skipped"

    # --------------------------------------------------------
    # CHANNEL JSON
    # --------------------------------------------------------

    channel_json_path = find_json_file(
        channel_dir,
        CHANNEL_JSON_FILENAME,
        CHANNEL_JSON_FALLBACK_NAMES,
    )

    if not channel_json_path:

        print("  ❌ Metadata channel " "tidak ditemukan.")

        return "missing"

    print("  Channel JSON: " + os.path.basename(channel_json_path))

    # --------------------------------------------------------
    # POPULAR VIDEOS
    # --------------------------------------------------------

    popular_videos_path = find_popular_videos_file(channel_dir)

    if not popular_videos_path:

        print("  ❌ Metadata video populer " "tidak ditemukan.")

        return "missing"

    print("  Videos JSON : " + os.path.basename(popular_videos_path))

    # --------------------------------------------------------
    # LOAD
    # --------------------------------------------------------

    try:

        channel_data = load_json_file(channel_json_path)

        popular_videos_data = load_json_file(popular_videos_path)

    except Exception as e:

        print(f"  ❌ Gagal membaca JSON: {e}")

        return "failed"

    # --------------------------------------------------------
    # BUILD DATA
    # --------------------------------------------------------

    analysis_data = build_analysis_data(
        channel_data,
        popular_videos_data,
    )

    prompt = build_model_prompt(
        channel_name,
        analysis_data,
    )

    # --------------------------------------------------------
    # CALL MODEL
    # --------------------------------------------------------

    print()
    print("  🚀 Menganalisis musical DNA " "dan dynamic variations...")

    try:

        raw_result = call_omniroute(
            base_url=base_url,
            api_key=api_key,
            model=model,
            prompt=prompt,
        )

    except Exception as e:

        print(f"  ❌ OmniRoute gagal: {e}")

        return "failed"

    # --------------------------------------------------------
    # PARSE
    # --------------------------------------------------------

    try:

        music_data = parse_music_json(raw_result)

        validate_music_json(music_data)

    except Exception as e:

        print(f"  ❌ JSON tidak valid: {e}")

        print("     Output TIDAK disimpan " "agar folder tetap bersih.")

        return "failed"

    # --------------------------------------------------------
    # SIMPAN SATU FILE SAJA
    # --------------------------------------------------------

    try:

        with open(
            output_path,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                music_data,
                f,
                ensure_ascii=False,
                indent=2,
            )

    except Exception as e:

        print(f"  ❌ Gagal menyimpan: {e}")

        return "failed"

    # --------------------------------------------------------
    # PREVIEW
    # --------------------------------------------------------

    dna = music_data["musical_dna"]

    variations = music_data["variations"]

    print()
    print("  ✅ variation_music.json " "berhasil dibuat.")

    print(f"     Genre    : {dna.get('genre')}")

    print(f"     Subgenre : {dna.get('subgenre')}")

    print(f"     Tempo    : {dna.get('tempo_bpm')}")

    print(f"     Mood     : {dna.get('mood')}")

    print(f"     Energy   : {dna.get('energy')}")

    print(f"     Instr.   : " f"{len(dna.get('instrumentation', []))}")

    print(f"     Keywords : " f"{len(music_data.get('keywords', []))}")

    print(f"     Negative : " f"{len(music_data.get('negative_keywords', []))}")

    print(
        f"     Variation: "
        f"{sum(len(v) for v in variations.values())}"
        " dynamic entries"
    )

    print(f"     Output   : {output_path}")

    return "done"


# ============================================================
# PROCESS ALL
# ============================================================


def process_all(
    output_dir: str,
    genre_folder: str,
    base_url: str,
    api_key: str,
    model: str,
    overwrite: bool,
):

    if not os.path.isdir(output_dir):

        print(f"❌ Folder '{output_dir}' " "tidak ditemukan.")

        sys.exit(1)

    # --------------------------------------------------------
    # GENRE
    # --------------------------------------------------------

    if genre_folder:

        target = os.path.join(
            output_dir,
            genre_folder,
        )

        if not os.path.isdir(target):

            print(f"❌ Folder genre '{target}' " "tidak ditemukan.")

            sys.exit(1)

        genre_dirs = [target]

    else:

        genre_dirs = [
            os.path.join(
                output_dir,
                dirname,
            )
            for dirname in sorted(os.listdir(output_dir))
            if os.path.isdir(
                os.path.join(
                    output_dir,
                    dirname,
                )
            )
        ]

    if not genre_dirs:

        print("❌ Tidak ada folder genre.")

        return

    total_done = 0
    total_skipped = 0
    total_failed = 0
    total_missing = 0

    # ========================================================
    # GENRE LOOP
    # ========================================================

    for genre_dir in genre_dirs:

        genre_name = os.path.basename(genre_dir)

        csv_path = find_outlier_csv(genre_dir)

        if not csv_path:

            print(f"[{genre_name}] " "Tidak ada outlier_*.csv.")

            continue

        channels = get_pass_channels(csv_path)

        if not channels:

            print(f"[{genre_name}] " "Tidak ada channel PASS.")

            continue

        print()
        print("=" * 80)
        print(f"[{genre_name}] " f"Ditemukan {len(channels)} " "channel PASS")
        print(f"CSV: {os.path.basename(csv_path)}")
        print("=" * 80)

        # ====================================================
        # CHANNEL LOOP
        # ====================================================

        for channel_name in channels:

            channel_dir = find_channel_dir(
                genre_dir,
                channel_name,
            )

            if not channel_dir:

                print()
                print(f"[{genre_name}] " f"{channel_name}")

                print("  ❌ Folder channel " "tidak ditemukan.")

                total_missing += 1

                continue

            result = process_channel(
                genre_name=genre_name,
                channel_name=channel_name,
                channel_dir=channel_dir,
                base_url=base_url,
                api_key=api_key,
                model=model,
                overwrite=overwrite,
            )

            if result == "done":

                total_done += 1

            elif result == "skipped":

                total_skipped += 1

            elif result == "missing":

                total_missing += 1

            else:

                total_failed += 1

            time.sleep(2)

    # ========================================================
    # SUMMARY
    # ========================================================

    print()
    print("=" * 80)
    print("RINGKASAN")
    print("=" * 80)

    print(f"Berhasil       : {total_done}")

    print(f"Dilewati       : {total_skipped}")

    print(f"Gagal          : {total_failed}")

    print(f"Data hilang    : {total_missing}")

    print("=" * 80)


# ============================================================
# MAIN
# ============================================================


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Generate variation_music.json " "berdasarkan musical DNA channel."
        )
    )

    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        help="Folder output utama.",
    )

    parser.add_argument(
        "--genre",
        default=GENRE_FOLDER,
        help=("Folder genre spesifik. " "Kosongkan untuk semua genre."),
    )

    parser.add_argument(
        "--base-url",
        default=OMNIROUTE_BASE_URL,
        help="Base URL OmniRoute.",
    )

    parser.add_argument(
        "--api-key",
        default=OMNIROUTE_API_KEY,
        help="API key OmniRoute.",
    )

    parser.add_argument(
        "--model",
        default=OMNIROUTE_MODEL,
        help="Model OmniRoute.",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help=("Generate ulang " "variation_music.json."),
    )

    args = parser.parse_args()

    if not args.api_key:

        print("❌ OMNIROUTE_API_KEY belum di-set.")

        print()
        print("Isi .env:")

        print("OMNIROUTE_API_KEY=api-key-kamu")

        sys.exit(1)

    process_all(
        output_dir=args.output_dir,
        genre_folder=args.genre,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        overwrite=args.overwrite,
    )


# ============================================================
# ENTRY POINT
# ============================================================


if __name__ == "__main__":

    main()
