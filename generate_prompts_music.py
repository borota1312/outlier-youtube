"""
generate_prompts_music.py
=========================

Scan folder:

output/[genre]/[nama_channel]/

Baca:

    variations_music.json

Lalu generate hingga N prompt musik unik berdasarkan:

    musical_dna
    keywords
    negative_keywords
    variations.arrangement
    variations.instrument_focus
    variations.groove
    variations.melodic
    variations.percussion
    variations.production
    variations.dynamics

Tidak ada variasi musik yang hard-code di script.
Semua variasi berasal dari variations_music.json.

Hasil disimpan sebagai:

    prompts_music.json

di folder channel yang sama.

Contoh struktur:

output/
  ambient/
    Healing Meditation Music/
      variations_music.json
      prompts_music.json


Contoh variations_music.json:

{
  "musical_dna": {
    "genre": "Ambient",
    "subgenre": "Healing meditation music",
    "tempo_bpm": "50-70",
    "rhythm": "Slow flowing minimal pulse",
    "groove": "Gentle sustained waves",
    "mood": "Peaceful calming restorative",
    "energy": "Low tranquil serene",
    "instrumentation": [
      "soft synthesizer pads",
      "delicate piano",
      "subtle nature sounds",
      "warm bass drones",
      "gentle bells",
      "atmospheric textures"
    ]
  },

  "keywords": [
    "healing ambient",
    "meditation soundscape",
    "gentle restorative",
    "soft emotional",
    "tranquil atmosphere"
  ],

  "negative_keywords": [
    "vocals",
    "lyrics",
    "spoken word",
    "percussion hits",
    "fast rhythm"
  ],

  "variations": {
    "arrangement": [
      "sparse minimalist layers",
      "gradual pad buildups"
    ],
    "instrument_focus": [
      "piano solo lead",
      "synthesizer wash dominant"
    ],
    "groove": [
      "completely static hold",
      "subtle breathing rhythm"
    ],
    "melodic": [
      "single note mantra",
      "pentatonic fragments"
    ],
    "percussion": [
      "no percussion silence",
      "rare distant chime"
    ],
    "production": [
      "heavy reverb space",
      "warm analog saturation"
    ],
    "dynamics": [
      "constant quiet level",
      "gentle swell and release"
    ]
  }
}


Cara pakai:

    python generate_prompts_music.py

    python generate_prompts_music.py --genre ambient

    python generate_prompts_music.py --genre ambient --count 45

    python generate_prompts_music.py --genre ambient --count 45 --overwrite

    python generate_prompts_music.py --count 100 --overwrite

"""

import argparse
import itertools
import json
import os
import random
import re
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# KONFIGURASI
# ============================================================

OMNIROUTE_API_KEY = os.getenv("OMNIROUTE_API_KEY")
OMNIROUTE_BASE_URL = "http://localhost:20128/v1"
OMNIROUTE_MODEL = "combo-analisis"

OUTPUT_DIR = "output"

# Nama genre yang mau diproses.
#
# Kosongkan "" untuk memproses semua genre.
#
# Contoh:
#
# GENRE_FOLDER = "afrobeats_affirmations"

GENRE_FOLDER = "ambient_dub"


# ============================================================
# JUMLAH PROMPT
# ============================================================

PROMPT_COUNT = 15


# ============================================================
# OVERWRITE
# ============================================================

# False:
# Skip jika prompts_music.json sudah ada.
#
# True:
# Selalu generate ulang.

OVERWRITE_EXISTING = False


# ============================================================
# RANDOM SEED
# ============================================================

# 42 = hasil kombinasi konsisten.
#
# None = kombinasi berbeda setiap kali dijalankan.

RANDOM_SEED = 42


# ============================================================
# FILE INPUT / OUTPUT
# ============================================================

MUSIC_DNA_FILENAME = "variation_music.json"

OUTPUT_FILENAME = "prompts_music.json"


# ============================================================
# KATEGORI VARIASI
# ============================================================

VARIATION_KEYS = [
    "arrangement",
    "instrument_focus",
    "groove",
    "melodic",
    "percussion",
    "production",
    "dynamics",
]


# ============================================================
# KONFIGURASI PAYLOAD BOPPY.ME API
# ============================================================

DEFAULT_MODEL = "AceStep_1_5_XL_Turbo_INT8"
DURATION_MIN = 180
DURATION_MAX = 240
DEFAULT_FORMAT = "mp3"
MAX_CAPTION_LENGTH = 300

LYRICS_PATTERNS = [
    "[instrument]",
    "[intro]\n[verse]\n[chorus]\n[outro]",
    "[instrumental intro]\n[verse]\n[chorus]\n[percussion drop]\n[chorus]\n[outro]",
    "[intro]\n[build-up]\n[verse]\n[chorus]\n[instrumental solo]\n[breakdown]\n[outro]",
    "[intro]\n[groove section]\n[main theme]\n[solo]\n[outro]",
    "[instrumental]\n[intro]\n[drop]\n[outro]",
]


def extract_bpm_integer(musical_dna: dict, rng: random.Random) -> int:
    """
    Mengambil BPM integer acak dari musical_dna (bpm_min/bpm_max atau tempo_bpm string).
    """
    bpm_min = musical_dna.get("bpm_min")
    bpm_max = musical_dna.get("bpm_max")

    if isinstance(bpm_min, int) and isinstance(bpm_max, int) and bpm_min <= bpm_max:
        return rng.randint(bpm_min, bpm_max)

    tempo_bpm_str = str(musical_dna.get("tempo_bpm", ""))
    numbers = [int(n) for n in re.findall(r"\d+", tempo_bpm_str)]
    if len(numbers) >= 2:
        start, end = min(numbers[0], numbers[1]), max(numbers[0], numbers[1])
        return rng.randint(start, end)
    elif len(numbers) == 1:
        return numbers[0]

    return 120


def build_caption_tags(
    musical_dna: dict, keywords: list, variation: dict, rng: random.Random
) -> str:
    """
    Membangun caption berupa tag terpisah koma dengan batas maksimal 300 karakter.
    """
    candidates = []

    # Priority 1: Genre & Subgenre
    if musical_dna.get("genre"):
        candidates.append(musical_dna["genre"])
    if musical_dna.get("subgenre"):
        candidates.append(musical_dna["subgenre"])

    # Priority 2: Mood & Energy
    if musical_dna.get("mood"):
        candidates.append(musical_dna["mood"])
    if musical_dna.get("energy"):
        candidates.append(f"{musical_dna['energy']} energy")

    # Priority 3: Instrumentation (Random 3-4 item)
    inst_list = list(musical_dna.get("instrumentation", []))
    if inst_list:
        rng.shuffle(inst_list)
        candidates.extend(inst_list[:4])

    # Priority 4: Keywords
    kw_list = list(keywords)
    if kw_list:
        rng.shuffle(kw_list)
        candidates.extend(kw_list)

    # Priority 5: Variasi dinamis
    var_values = [variation[k] for k in VARIATION_KEYS if k in variation]
    rng.shuffle(var_values)
    candidates.extend(var_values)

    # Clean & Deduplicate candidates
    seen = set()
    cleaned_candidates = []
    for cand in candidates:
        cand_clean = sanitize_text(cand)
        if cand_clean and cand_clean.lower() not in seen:
            seen.add(cand_clean.lower())
            cleaned_candidates.append(cand_clean)

    # Assemble caption with max 300 characters limit
    result_tags = []
    current_length = 0

    for tag in cleaned_candidates:
        added_len = len(tag) + (2 if result_tags else 0)
        if current_length + added_len > MAX_CAPTION_LENGTH:
            break
        result_tags.append(tag)
        current_length += added_len

    if not result_tags and cleaned_candidates:
        result_tags.append(cleaned_candidates[0][:MAX_CAPTION_LENGTH])

    return ", ".join(result_tags)


def generate_music_title(
    caption: str,
    base_url: str = OMNIROUTE_BASE_URL,
    api_key: str = OMNIROUTE_API_KEY,
    model: str = OMNIROUTE_MODEL,
) -> str:
    """
    Menanyakan ke model AI untuk merekomendasikan judul musik 2-3 kata berdasarkan caption.
    """
    if not caption:
        return "Golden Afrobeat Vibe"

    prompt_text = (
        f"Give a single catchy music track title in EXACTLY 2 or 3 words based on these tags:\n'{caption}'\n\n"
        f"Rules:\n"
        f"1. Output ONLY the 2 to 3 words title.\n"
        f"2. Do NOT provide options, notes, or explanation.\n"
        f"3. Do NOT use quotes, asterisks, or markdown formatting."
    )

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt_text}],
        "temperature": 0.7,
        "stream": True,
    }

    try:
        resp = requests.post(
            url, headers=headers, json=payload, timeout=30, stream=True
        )
        resp.raise_for_status()

        chunks = []
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
                choices = data.get("choices", [])
                if choices:
                    delta_content = choices[0].get("delta", {}).get("content")
                    if delta_content:
                        chunks.append(delta_content)
            except Exception:
                continue

        raw_title = "".join(chunks).strip()
        if "<think>" in raw_title:
            raw_title = re.sub(
                r"<think>.*?</think>", "", raw_title, flags=re.DOTALL
            ).strip()

        raw_title = re.sub(r'[`"\'*#_.]', "", raw_title).strip()
        lines = [ln.strip() for ln in raw_title.splitlines() if ln.strip()]
        if lines:
            first_line = lines[0]
            words = first_line.split()
            if len(words) >= 1:
                title_words = words[:3] if len(words) >= 3 else words
                return " ".join(title_words).title()
    except Exception:
        pass

    # Fallback jika API error/timeout
    tags = [t.strip() for t in caption.split(",") if t.strip()]
    if len(tags) >= 2:
        return f"{tags[0]} {tags[1]}".title()[:30]
    return "Golden Afrobeat Vibe"


# ============================================================
# TEMPLATE PROMPT
# ============================================================

PROMPT_TEMPLATE = (
    "Create an instrumental {genre} track in the {subgenre} style, "
    "{tempo_bpm}, with {rhythm} and {base_groove}. "
    "The mood should be {mood}, with {energy} energy. "
    "Use {instrumentation}. "
    "{arrangement}, {instrument_focus}, {groove}, "
    "{melodic}, {percussion}, {production}, and {dynamics}. "
    "Musical direction: {keywords}. "
    "Instrumental only, no {negative_keywords}."
)


# ============================================================
# SANITASI TEXT
# ============================================================


def sanitize_text(text: str) -> str:
    """
    Membersihkan whitespace berlebihan.
    """

    if not isinstance(text, str):
        return ""

    text = " ".join(text.split())

    return text.strip()


# ============================================================
# LOAD JSON
# ============================================================


def load_json_file(path: str):
    """
    Membaca JSON.
    """

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as f:

        return json.load(f)


# ============================================================
# FIND CHANNEL DIRECTORIES
# ============================================================


def find_channel_dirs(
    output_dir: str,
    genre_folder: str,
):
    """
    Kembalikan list:

        (
            genre_name,
            channel_name,
            channel_dir
        )

    untuk folder channel yang memiliki:

        variations_music.json
    """

    if genre_folder:

        genre_dir = os.path.join(
            output_dir,
            genre_folder,
        )

        if not os.path.isdir(genre_dir):

            return []

        genre_dirs = [genre_dir]

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

    results = []

    for genre_dir in genre_dirs:

        if not os.path.isdir(genre_dir):
            continue

        genre_name = os.path.basename(genre_dir)

        try:

            entries = sorted(os.listdir(genre_dir))

        except OSError:

            continue

        for entry in entries:

            channel_dir = os.path.join(
                genre_dir,
                entry,
            )

            if not os.path.isdir(channel_dir):
                continue

            music_dna_path = os.path.join(
                channel_dir,
                MUSIC_DNA_FILENAME,
            )

            if os.path.isfile(music_dna_path):

                results.append(
                    (
                        genre_name,
                        entry,
                        channel_dir,
                    )
                )

    return results


# ============================================================
# CLEAN LIST
# ============================================================


def clean_string_list(
    value,
    field_name: str,
):
    """
    Membersihkan list string:

    - buang item non-string
    - buang string kosong
    - trim whitespace
    - hapus duplikat
    """

    if not isinstance(
        value,
        list,
    ):

        raise ValueError(f"Field '{field_name}' " f"harus berupa array/list.")

    cleaned = []

    for item in value:

        if not isinstance(
            item,
            str,
        ):
            continue

        item = sanitize_text(item)

        if not item:
            continue

        if item not in cleaned:

            cleaned.append(item)

    if not cleaned:

        raise ValueError(f"Field '{field_name}' " f"tidak memiliki item valid.")

    return cleaned


# ============================================================
# LOAD MUSICAL DNA
# ============================================================


def load_music_dna(
    channel_dir: str,
):
    """
    Membaca variations_music.json.

    Mengambil:

        musical_dna
        keywords
        negative_keywords
        variations
    """

    path = os.path.join(
        channel_dir,
        MUSIC_DNA_FILENAME,
    )

    if not os.path.isfile(path):

        raise FileNotFoundError(f"{MUSIC_DNA_FILENAME} " f"tidak ditemukan: {path}")

    data = load_json_file(path)

    if not isinstance(
        data,
        dict,
    ):

        raise ValueError("variations_music.json " "harus berupa JSON object.")

    # ========================================================
    # MUSICAL DNA
    # ========================================================

    musical_dna = data.get("musical_dna")

    if not isinstance(
        musical_dna,
        dict,
    ):

        raise ValueError("Field 'musical_dna' " "tidak ditemukan atau bukan object.")

    required_dna_fields = [
        "genre",
        "subgenre",
        "tempo_bpm",
        "rhythm",
        "groove",
        "mood",
        "energy",
        "instrumentation",
    ]

    for field in required_dna_fields:

        if field not in musical_dna:

            raise ValueError(f"Field musical_dna." f"'{field}' tidak ditemukan.")

    # ========================================================
    # BASIC DNA VALUES
    # ========================================================

    dna = {}

    for field in [
        "genre",
        "subgenre",
        "tempo_bpm",
        "rhythm",
        "groove",
        "mood",
        "energy",
    ]:

        value = musical_dna.get(field)

        if not isinstance(
            value,
            str,
        ):

            raise ValueError(f"musical_dna.{field} " f"harus berupa string.")

        value = sanitize_text(value)

        if not value:

            raise ValueError(f"musical_dna.{field} " f"tidak boleh kosong.")

        dna[field] = value

    # ========================================================
    # INSTRUMENTATION
    # ========================================================

    dna["instrumentation"] = clean_string_list(
        musical_dna.get("instrumentation"),
        "musical_dna.instrumentation",
    )

    # ========================================================
    # KEYWORDS
    # ========================================================

    keywords = clean_string_list(
        data.get("keywords"),
        "keywords",
    )

    # ========================================================
    # NEGATIVE KEYWORDS
    # ========================================================

    negative_keywords = clean_string_list(
        data.get("negative_keywords"),
        "negative_keywords",
    )

    # ========================================================
    # VARIATIONS
    # ========================================================

    variations = data.get("variations")

    if not isinstance(
        variations,
        dict,
    ):

        raise ValueError("Field 'variations' " "tidak ditemukan atau bukan object.")

    cleaned_variations = {}

    for key in VARIATION_KEYS:

        if key not in variations:

            raise ValueError(f"Variasi '{key}' " f"tidak ditemukan.")

        cleaned_variations[key] = clean_string_list(
            variations[key],
            f"variations.{key}",
        )

    return {
        "musical_dna": dna,
        "keywords": keywords,
        "negative_keywords": negative_keywords,
        "variations": cleaned_variations,
    }


# ============================================================
# GENERATE UNIQUE COMBINATIONS
# ============================================================


def generate_unique_variations(
    variations: dict,
    count: int,
    seed=None,
):
    """
    Menghasilkan kombinasi unik dari seluruh kategori:

        arrangement
        instrument_focus
        groove
        melodic
        percussion
        production
        dynamics

    Contoh:

        6 × 6 × 6 × 6 × 6 × 6 × 6

    = 279,936 kombinasi.

    Jadi jumlah prompt bisa sangat besar
    tanpa perlu menambah hard-coded variation.
    """

    if count <= 0:

        raise ValueError("Jumlah prompt harus lebih " "besar dari 0.")

    # ========================================================
    # RANDOM INSTANCE
    # ========================================================

    rng = random.Random(seed)

    # ========================================================
    # TOTAL COMBINATION
    # ========================================================

    lengths = [len(variations[key]) for key in VARIATION_KEYS]

    total_possible = 1

    for length in lengths:

        total_possible *= length

    # ========================================================
    # VALIDATE COUNT
    # ========================================================

    if count > total_possible:

        raise ValueError(
            f"Diminta {count} prompt unik, "
            f"tetapi hanya tersedia "
            f"{total_possible} kombinasi unik."
        )

    # ========================================================
    # Jika kombinasi tidak terlalu besar,
    # kita bisa generate seluruh kombinasi.
    # ========================================================

    # Untuk ukuran besar, jangan membuat list
    # jutaan kombinasi ke memory.
    #
    # Jika total <= 1.000.000,
    # gunakan itertools.product.
    #
    # Jika lebih besar,
    # gunakan sampling index kombinasi.

    if total_possible <= 1_000_000:

        all_combos = itertools.product(*[variations[key] for key in VARIATION_KEYS])

        all_combos = list(all_combos)

        rng.shuffle(all_combos)

        chosen = all_combos[:count]

        return [
            dict(
                zip(
                    VARIATION_KEYS,
                    combo,
                )
            )
            for combo in chosen
        ]

    # ========================================================
    # LARGE COMBINATION SPACE
    # ========================================================

    # Ambil kombinasi berdasarkan
    # random integer index tanpa membuat
    # seluruh Cartesian product.

    selected_indexes = set()

    while len(selected_indexes) < count:

        selected_indexes.add(rng.randrange(total_possible))

    selected_indexes = list(selected_indexes)

    rng.shuffle(selected_indexes)

    results = []

    for index in selected_indexes:

        values = []

        remaining = index

        # ----------------------------------------------------
        # Convert integer index menjadi
        # koordinat Cartesian product.
        # ----------------------------------------------------

        for length in reversed(lengths):

            position = remaining % length

            remaining //= length

            values.append(position)

        values.reverse()

        combo = {}

        for i, key in enumerate(VARIATION_KEYS):

            combo[key] = variations[key][values[i]]

        results.append(combo)

    return results


# ============================================================
# BUILD PROMPT
# ============================================================


def build_prompt(
    musical_dna: dict,
    keywords: list,
    negative_keywords: list,
    variation: dict,
):
    """
    Membuat satu prompt musik berdasarkan:

        musical_dna
        keywords
        negative_keywords
        satu kombinasi variation
    """

    instrumentation = ", ".join(musical_dna["instrumentation"])

    keyword_text = ", ".join(keywords)

    negative_text = ", ".join(negative_keywords)

    prompt = PROMPT_TEMPLATE.format(
        genre=musical_dna["genre"],
        subgenre=musical_dna["subgenre"],
        tempo_bpm=musical_dna["tempo_bpm"],
        rhythm=musical_dna["rhythm"],
        base_groove=musical_dna["groove"],
        mood=musical_dna["mood"],
        energy=musical_dna["energy"],
        instrumentation=instrumentation,
        arrangement=variation["arrangement"],
        instrument_focus=variation["instrument_focus"],
        groove=variation["groove"],
        melodic=variation["melodic"],
        percussion=variation["percussion"],
        production=variation["production"],
        dynamics=variation["dynamics"],
        keywords=keyword_text,
        negative_keywords=negative_text,
    )

    return sanitize_text(prompt)


# ============================================================
# BUILD ALL PROMPTS
# ============================================================


def build_prompts(
    music_data: dict,
    count: int,
    seed=None,
    base_url: str = OMNIROUTE_BASE_URL,
    api_key: str = OMNIROUTE_API_KEY,
    model: str = OMNIROUTE_MODEL,
):
    """
    Generate prompt musik unik.
    """

    musical_dna = music_data["musical_dna"]

    keywords = music_data["keywords"]

    negative_keywords = music_data["negative_keywords"]

    variations = music_data["variations"]

    rng = random.Random(seed)

    # ========================================================
    # COMBINATIONS
    # ========================================================

    combinations = generate_unique_variations(
        variations=variations,
        count=count,
        seed=seed,
    )

    prompts = []
    title_counter = {}

    for index, combination in enumerate(
        combinations,
        1,
    ):

        prompt = build_prompt(
            musical_dna=musical_dna,
            keywords=keywords,
            negative_keywords=negative_keywords,
            variation=combination,
        )

        bpm_val = extract_bpm_integer(musical_dna, rng)
        duration_val = rng.randint(DURATION_MIN, DURATION_MAX)
        caption_val = build_caption_tags(musical_dna, keywords, combination, rng)
        lyrics_val = rng.choice(LYRICS_PATTERNS)
        raw_title_val = generate_music_title(
            caption=caption_val,
            base_url=base_url,
            api_key=api_key,
            model=model,
        )

        # Ensure unique title with suffix index if duplicate
        title_counter[raw_title_val] = title_counter.get(raw_title_val, 0) + 1
        count_occur = title_counter[raw_title_val]
        if count_occur > 1:
            final_title_val = f"{raw_title_val} {count_occur}"
        else:
            final_title_val = raw_title_val

        payload_obj = {
            "caption": caption_val,
            "lyrics": lyrics_val,
            "model": DEFAULT_MODEL,
            "duration": duration_val,
            "bpm": bpm_val,
            "format": DEFAULT_FORMAT,
        }

        prompts.append(
            {
                "index": index,
                "title": final_title_val,
                "bpm": bpm_val,
                "duration": duration_val,
                "caption": caption_val,
                "lyrics": lyrics_val,
                "arrangement": combination["arrangement"],
                "instrument_focus": combination["instrument_focus"],
                "groove": combination["groove"],
                "melodic": combination["melodic"],
                "percussion": combination["percussion"],
                "production": combination["production"],
                "dynamics": combination["dynamics"],
                "prompt": prompt,
                "payload": payload_obj,
            }
        )

    return prompts


# ============================================================
# CALCULATE POSSIBLE COMBINATIONS
# ============================================================


def calculate_possible_combinations(
    variations: dict,
):
    """
    Hitung jumlah kombinasi yang tersedia.
    """

    total = 1

    for key in VARIATION_KEYS:

        total *= len(variations[key])

    return total


# ============================================================
# PROCESS ALL CHANNEL
# ============================================================


def process_all(
    output_dir: str,
    genre_folder: str,
    count: int,
    overwrite: bool,
    seed,
    base_url: str = OMNIROUTE_BASE_URL,
    api_key: str = OMNIROUTE_API_KEY,
    model: str = OMNIROUTE_MODEL,
):
    """
    Process seluruh channel.
    """

    # ========================================================
    # OUTPUT DIRECTORY
    # ========================================================

    if not os.path.isdir(output_dir):

        print(f"Folder '{output_dir}' " f"tidak ditemukan.")

        sys.exit(1)

    # ========================================================
    # CHANNEL DIRECTORIES
    # ========================================================

    channel_dirs = find_channel_dirs(
        output_dir=output_dir,
        genre_folder=genre_folder,
    )

    if not channel_dirs:

        print("Tidak ada folder channel " f"yang memiliki " f"{MUSIC_DNA_FILENAME}.")

        print()

        print("Pastikan struktur seperti:")

        print("output/ambient/" "Nama Channel/")

        print("└── variations_music.json")

        return

    # ========================================================
    # COUNTERS
    # ========================================================

    total_done = 0
    total_skipped = 0
    total_failed = 0

    current_genre = None

    # ========================================================
    # LOOP CHANNEL
    # ========================================================

    for (
        genre_name,
        channel_name,
        channel_dir,
    ) in channel_dirs:

        if genre_name != current_genre:

            current_genre = genre_name

            print()

            print(f"[{genre_name}]")

        output_path = os.path.join(
            channel_dir,
            OUTPUT_FILENAME,
        )

        # ====================================================
        # SKIP EXISTING
        # ====================================================

        if os.path.isfile(output_path) and not overwrite:

            print(
                f"  - [{channel_name}] "
                f"SKIP: "
                f"{OUTPUT_FILENAME} "
                f"sudah ada "
                f"(pakai --overwrite "
                f"untuk timpa)"
            )

            total_skipped += 1

            continue

        print(
            f"  - [{channel_name}] " f"Generate {count} " f"prompt...",
            end=" ",
            flush=True,
        )

        try:

            # ================================================
            # LOAD
            # ================================================

            music_data = load_music_dna(channel_dir)

            variations = music_data["variations"]

            # ================================================
            # POSSIBLE COMBINATIONS
            # ================================================

            possible_combinations = calculate_possible_combinations(variations)

            # ================================================
            # BUILD PROMPTS
            # ================================================

            prompts = build_prompts(
                music_data=music_data,
                count=count,
                seed=seed,
                base_url=base_url,
                api_key=api_key,
                model=model,
            )

            # ================================================
            # OUTPUT
            # ================================================

            output_data = {
                "channel": channel_name,
                "genre": music_data["musical_dna"]["genre"],
                "subgenre": music_data["musical_dna"]["subgenre"],
                "source": MUSIC_DNA_FILENAME,
                "variation_count": {
                    key: len(variations[key]) for key in VARIATION_KEYS
                },
                "possible_combinations": (possible_combinations),
                "prompt_count": len(prompts),
                "prompts": prompts,
            }

            # ================================================
            # SAVE
            # ================================================

            with open(
                output_path,
                "w",
                encoding="utf-8",
            ) as f:

                json.dump(
                    output_data,
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

            # ================================================
            # STATUS
            # ================================================

            counts = " × ".join(str(len(variations[key])) for key in VARIATION_KEYS)

            print(f"OK " f"({counts} " f"= {possible_combinations:,} " f"kombinasi)")

            total_done += 1

        except Exception as e:

            print(f"GAGAL ({e})")

            total_failed += 1

    # ========================================================
    # SUMMARY
    # ========================================================

    print()

    print("=== Ringkasan ===")

    print(f"Berhasil : {total_done}")

    print(f"Dilewati : {total_skipped}")

    print(f"Gagal    : {total_failed}")


# ============================================================
# MAIN
# ============================================================


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Generate prompt musik unik " "berdasarkan " "variations_music.json."
        )
    )

    # ========================================================
    # OUTPUT
    # ========================================================

    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        help=("Folder induk output."),
    )

    # ========================================================
    # GENRE
    # ========================================================

    parser.add_argument(
        "--genre",
        default=GENRE_FOLDER,
        help=("Nama folder genre spesifik. " "Kosongkan untuk semua genre."),
    )

    # ========================================================
    # COUNT
    # ========================================================

    parser.add_argument(
        "--count",
        type=int,
        default=PROMPT_COUNT,
        help=("Jumlah prompt unik " "per channel."),
    )

    # ========================================================
    # OVERWRITE
    # ========================================================

    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=OVERWRITE_EXISTING,
        help=("Timpa prompts_music.json " "jika sudah ada."),
    )

    # ========================================================
    # SEED
    # ========================================================

    parser.add_argument(
        "--seed",
        type=int,
        default=RANDOM_SEED,
        help=("Seed random. " "Gunakan seed berbeda untuk " "kombinasi berbeda."),
    )

    parser.add_argument(
        "--base-url",
        default=OMNIROUTE_BASE_URL,
        help=f"Base URL OmniRoute (default: {OMNIROUTE_BASE_URL})",
    )
    parser.add_argument(
        "--api-key",
        default=OMNIROUTE_API_KEY,
        help="API key OmniRoute (default: dari .env)",
    )
    parser.add_argument(
        "--model",
        default=OMNIROUTE_MODEL,
        help=f"Model OmniRoute (default: {OMNIROUTE_MODEL})",
    )

    args = parser.parse_args()

    # ========================================================
    # VALIDATE COUNT
    # ========================================================

    if args.count <= 0:

        print("❌ --count harus lebih " "besar dari 0.")

        sys.exit(1)

    # ========================================================
    # PROCESS
    # ========================================================

    process_all(
        output_dir=args.output_dir,
        genre_folder=args.genre,
        count=args.count,
        overwrite=args.overwrite,
        seed=args.seed,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
