"""
genre_bank_analyzer.py
======================

Build & maintain a "genre bank" of niche/underground music genres using a
LOCAL LLM via OmniRoute (http://localhost:20128/v1) and analyze monetization
potential via YouTube API.

Alur Kerja:
1. LLM Discovery: Panggil OmniRoute lokal untuk mencari 5-10 genre niche baru.
2. Tambahkan genre baru ke output/genre_bank.csv (additive-only, tidak ada hapus).
3. YouTube API Analysis: Otomatis analisis monetisasi untuk genre yang belum punya nilai
   (atau SEMUA genre jika menggunakan --reanalyze).

Cara pakai:
    python3 genre_bank_analyzer.py               # Discovery LLM + Auto Analisis YouTube API
    python3 genre_bank_analyzer.py --no-llm      # Analisis YouTube API untuk genre yang belum dinilai
    python3 genre_bank_analyzer.py --reanalyze   # Update data YouTube API untuk SEMUA genre di bank
    python3 genre_bank_analyzer.py --list        # Tampilkan tabel genre terurut dari score tertinggi
"""

import argparse
import csv
import datetime
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from typing import Optional

import requests
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

OMNIROUTE_API_KEY = os.getenv("OMNIROUTE_API_KEY")
OMNIROUTE_BASE_URL = "http://localhost:20128/v1"
OMNIROUTE_MODEL = "baifree"
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

OUTPUT_DIR = "output"
GENRE_BANK_CSV = os.path.join(OUTPUT_DIR, "genre_bank.csv")

REQUEST_TIMEOUT = 180
MAX_ATTEMPTS = 3
DELAY_BETWEEN_RUNS = 2
TIME_WINDOW_DAYS = 60

CSV_FIELDNAMES = [
    "genre",
    "analysis_status",
    "channel_count",
    "avg_views",
    "viability_index",
    "longform_ratio_pct",
    "top3_share_pct",
    "engagement_rate_pct",
    "avg_view_velocity",
    "competition_status",
    "monetization_score",
    "recommendation",
    "analyzed_at",
]

DISCOVERY_PROMPT = """You are a knowledgeable underground/niche music curator, up to date with the 2026 scene. Your task is to help me explore non-mainstream music genres.

CONTEXT:
I'm interested in music genres that are far from mainstream radar — things like shamanic music, afrobeat (not afrobeats), soul funk, phonk, breakcore, dungeon synth, and similar. I want genres that are genuinely niche, not ones that have already gone viral or are commonly discussed.

KNOWN GENRES (already in my collection — do NOT include any of these):
{known_genres}

INSTRUCTIONS:
1. Search for and list 5-10 niche/underground music genres covering a mix of: traditional world & ethnic music, funk-soul-jazz fusion/derivatives, electronic & experimental, phonk/trap/underground hip-hop derivatives, and genres trending in the underground scene.

2. Present each genre as ONE single-line entry combining all details: genre name (bold) — parent genre/roots — sound characteristics/mood — 2-3 representative active artists — region/scene of origin — platform where it thrives.

3. Do NOT add "Option 1:", "Genre 1:", "Item 1:", or any prefix label before the genre name. Do NOT group genres into categories or sections. Do NOT use nested/multi-level bullet points. Every genre must be a flat, standalone bullet point at the same level starting directly with **Genre Name**.

4. Prioritize genres that:
 - Have a small but loyal/dedicated fanbase
 - Are rarely discussed on mainstream music platforms (Spotify Top Charts, radio, etc.)
 - Have a strong visual/aesthetic identity (if applicable)

5. Search for CURRENT information (browsing/web search if available) to ensure the genres and artists mentioned are still relevant/active this year, not just based on outdated data.

6. End with one flat line: "Most niche of all: [genre 1], [genre 2], [genre 3]" listing the 3-5 genres I've likely never heard of at all.

OUTPUT FORMAT:
A single flat bullet list, one genre per line, no headings, no categories, no sub-bullets. Format each line as:
**Genre Name** (root genre) — description — Artists: X, Y, Z — Origin: [region] — Scene: [platform]

Keep each line concise, dense, and consistent in structure across all entries."""


def build_discovery_prompt(known_genres: list) -> str:
    known_block = ", ".join(known_genres) if known_genres else "(none yet — first run)"
    return DISCOVERY_PROMPT.format(known_genres=known_block)


def call_omniroute(base_url: str, api_key: str, model: str, prompt_text: str) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt_text}],
        "stream": True,
    }
    resp = requests.post(
        url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT, stream=True
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
        except Exception:
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


def normalize_genre_key(genre: str) -> str:
    return re.sub(r"\s+", " ", genre.strip().lower())


PLACEHOLDER_GENRES = {"option", "example", "genre", "item", "choice", "n/a", "unknown"}


def is_placeholder_genre(genre_name: str) -> bool:
    low = genre_name.lower().strip()
    if low in PLACEHOLDER_GENRES:
        return True
    if re.match(r"^(option|genre|item|choice|example)\s*\d+$", low, re.IGNORECASE):
        return True
    return False


def strip_list_prefix(line: str) -> str:
    line = re.sub(
        r"^(?:option|genre|item|choice|example)\s*\d+[\.\:\-]?\s*",
        "",
        line,
        flags=re.IGNORECASE,
    )
    line = re.sub(r"^[\*\•\-\–\—\>\+]\s+", "", line)
    line = re.sub(r"^\d+[\.\)]\s+", "", line)
    return line.strip()


def looks_like_genre_line(line: str) -> bool:
    low = line.lower()
    has_fields = any(f in low for f in ("artists:", "origin:", "scene:"))
    has_dashes = len(re.findall(r"—|–", line)) >= 2
    return has_fields or has_dashes


def parse_line(line: str) -> Optional[dict]:
    genre = ""
    m = re.search(r"\*\*(?P<genre>.+?)\*\*", line)
    if m:
        genre = m.group("genre").strip()
    else:
        m = re.search(r"\*(?P<genre>[^*]+?)\*", line)
        if m:
            genre = m.group("genre").strip()
        elif looks_like_genre_line(line):
            parts = re.split(r"—|–", line, maxsplit=1)
            genre = parts[0].strip().strip("*").strip()
            genre = re.sub(r"^[^\w(]+", "", genre).strip()
            pm_name = re.search(r"\((?P<parent>[^)]+)\)", genre)
            if pm_name:
                genre = (genre[: pm_name.start()] + genre[pm_name.end() :]).strip()
        else:
            return None

    genre = genre.strip().strip("*").rstrip(":").strip()
    if not genre or len(genre) < 2 or is_placeholder_genre(genre):
        return None

    return {"genre": genre}


def parse_llm_output(text: str) -> list:
    entries = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("```") or line.startswith("#"):
            continue
        if re.match(r"^[\*\•\-\s]*most\s+niche\s+of\s+all\s*:\s*", line, re.IGNORECASE):
            continue

        line = strip_list_prefix(line)
        entry = parse_line(line)
        if entry:
            entries.append(entry)

    return entries


def parse_iso8601_duration(duration_str: str) -> int:
    match = re.match(
        r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        duration_str,
    )
    if not match:
        return 0
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return hours * 3600 + minutes * 60 + seconds


def analyze_genre_youtube(
    genre: str, api_key: str, days: int = TIME_WINDOW_DAYS
) -> Optional[dict]:
    published_after = (
        (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days))
        .isoformat()
        .replace("+00:00", "Z")
    )

    youtube = build("youtube", "v3", developerKey=api_key)

    query = f"ai {genre} music"
    try:
        search_req = youtube.search().list(
            q=query,
            part="snippet",
            type="video",
            maxResults=50,
            order="viewCount",
            publishedAfter=published_after,
        )
        search_resp = search_req.execute()
    except HttpError as e:
        print(f"    API Error pada search untuk '{genre}': {e}")
        return None

    items = search_resp.get("items", [])
    if not items:
        return None

    video_ids = [item["id"]["videoId"] for item in items]
    channel_ids = list(set(item["snippet"]["channelId"] for item in items))

    try:
        vid_req = youtube.videos().list(
            part="statistics,contentDetails", id=",".join(video_ids)
        )
        vid_resp = vid_req.execute()
    except HttpError as e:
        print(f"    API Error pada video stats untuk '{genre}': {e}")
        return None

    vid_stats_map = {v["id"]: v for v in vid_resp.get("items", [])}

    chan_stats_map = {}
    if channel_ids:
        try:
            chan_req = youtube.channels().list(
                part="statistics", id=",".join(channel_ids[:50])
            )
            chan_resp = chan_req.execute()
            chan_stats_map = {c["id"]: c for c in chan_resp.get("items", [])}
        except HttpError as e:
            print(f"    API Error pada channel stats untuk '{genre}': {e}")

    video_data = []
    channel_views = Counter()

    for item in items:
        v_id = item["id"]["videoId"]
        v_stat = vid_stats_map.get(v_id)
        if not v_stat:
            continue

        stats = v_stat.get("statistics", {})
        view_count = int(stats.get("viewCount", 0))
        if view_count == 0:
            continue

        like_count = int(stats.get("likeCount", 0))
        comment_count = int(stats.get("commentCount", 0))
        duration_str = v_stat.get("contentDetails", {}).get("duration", "")
        duration_sec = parse_iso8601_duration(duration_str)

        ch_id = item["snippet"]["channelId"]
        channel_views[ch_id] += view_count

        published_at = item["snippet"]["publishedAt"]
        days_old = max(
            1,
            (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.datetime.strptime(
                    published_at, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=datetime.timezone.utc)
            ).days,
        )

        video_data.append(
            {
                "view_count": view_count,
                "like_count": like_count,
                "comment_count": comment_count,
                "duration_sec": duration_sec,
                "days_old": days_old,
                "channel_id": ch_id,
            }
        )

    if not video_data:
        return None

    total_videos = len(video_data)
    total_views = sum(v["view_count"] for v in video_data)
    total_likes = sum(v["like_count"] for v in video_data)
    total_comments = sum(v["comment_count"] for v in video_data)
    unique_channels = set(v["channel_id"] for v in video_data)
    channel_count = len(unique_channels)

    avg_views = int(total_views / total_videos)

    sub_counts = []
    for ch_id in unique_channels:
        ch_info = chan_stats_map.get(ch_id)
        if ch_info:
            c_sub = int(ch_info.get("statistics", {}).get("subscriberCount", 0))
            if c_sub > 0:
                sub_counts.append(c_sub)

    avg_subs = statistics.mean(sub_counts) if sub_counts else 0
    viability_index = round(avg_views / max(1, avg_subs), 2)

    longform_count = sum(1 for v in video_data if v["duration_sec"] >= 480)
    longform_ratio_pct = round((longform_count / total_videos) * 100, 1)

    top3_views = sum(count for _, count in channel_views.most_common(3))
    top3_share_pct = (
        round((top3_views / total_views) * 100, 1) if total_views > 0 else 0
    )

    engagement_rate_pct = (
        round(((total_likes + total_comments) / total_views) * 100, 2)
        if total_views > 0
        else 0
    )

    velocities = [v["view_count"] / v["days_old"] for v in video_data]
    avg_view_velocity = int(statistics.mean(velocities)) if velocities else 0

    if top3_share_pct < 85:
        competition_status = "Blue Ocean"
    elif top3_share_pct > 95:
        competition_status = "Monopolized"
    else:
        competition_status = "Moderate"

    score_viability = min(100, viability_index * 40)
    score_longform = longform_ratio_pct
    if top3_share_pct <= 80:
        score_competition = 100
    elif top3_share_pct >= 98:
        score_competition = 0
    else:
        score_competition = round((98 - top3_share_pct) / 18 * 100, 1)

    score_engagement = min(100, engagement_rate_pct * 25)
    score_velocity = min(100, (avg_view_velocity / 5000) * 100)

    monetization_score = round(
        score_viability * 0.25
        + score_longform * 0.20
        + score_competition * 0.25
        + score_engagement * 0.15
        + score_velocity * 0.15,
        1,
    )

    if monetization_score >= 80:
        recommendation = "Highly Recommended"
    elif monetization_score >= 60:
        recommendation = "Viable"
    elif monetization_score >= 40:
        recommendation = "High Risk"
    else:
        recommendation = "Not Recommended"

    return {
        "genre": genre,
        "analysis_status": "analyzed",
        "channel_count": channel_count,
        "avg_views": avg_views,
        "viability_index": viability_index,
        "longform_ratio_pct": longform_ratio_pct,
        "top3_share_pct": top3_share_pct,
        "engagement_rate_pct": engagement_rate_pct,
        "avg_view_velocity": avg_view_velocity,
        "competition_status": competition_status,
        "monetization_score": monetization_score,
        "recommendation": recommendation,
        "analyzed_at": datetime.date.today().isoformat(),
    }


def load_existing_bank(csv_path: str) -> dict:
    if not os.path.exists(csv_path):
        return {}

    existing = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = normalize_genre_key(row["genre"])
            existing[key] = row
    return existing


def save_full_bank(bank_dict: dict, csv_path: str):
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for row in bank_dict.values():
            writer.writerow(row)


def append_genre_row(genre_name: str, csv_path: str, bank_dict: dict) -> dict:
    key = normalize_genre_key(genre_name)
    if key in bank_dict:
        return bank_dict[key]

    new_row = {
        "genre": genre_name,
        "analysis_status": "pending",
        "channel_count": "",
        "avg_views": "",
        "viability_index": "",
        "longform_ratio_pct": "",
        "top3_share_pct": "",
        "engagement_rate_pct": "",
        "avg_view_velocity": "",
        "competition_status": "",
        "monetization_score": "",
        "recommendation": "",
        "analyzed_at": "",
    }
    bank_dict[key] = new_row
    save_full_bank(bank_dict, csv_path)
    return new_row


def update_genre_row(genre_key: str, updated_row: dict, csv_path: str, bank_dict: dict):
    bank_dict[genre_key] = updated_row
    save_full_bank(bank_dict, csv_path)


def print_bank(csv_path: str):
    bank_dict = load_existing_bank(csv_path)
    if not bank_dict:
        print(f"Genre bank kosong. ({csv_path})")
        return

    rows = list(bank_dict.values())

    def safe_score(row):
        val = row.get("monetization_score")
        try:
            return float(val)
        except (ValueError, TypeError):
            return -1.0

    rows.sort(key=safe_score, reverse=True)

    print(f"\nGENRE BANK — {len(rows)} genre tersimpan ({csv_path})")
    print("=" * 130)
    print(
        f"{'Rank':<5} {'Genre':<26} {'Status':<11} {'Score':<8} {'Comp Status':<14} {'Viability':<10} {'Longform':<10} {'Top3 Share':<12} {'Recommendation':<20}"
    )
    print("-" * 130)

    for i, row in enumerate(rows, 1):
        astatus_str = str(row.get("analysis_status", "pending"))
        score_str = str(row.get("monetization_score", ""))
        status_str = str(row.get("competition_status", ""))
        viab_str = str(row.get("viability_index", ""))
        long_str = (
            f"{row.get('longform_ratio_pct', '')}%"
            if row.get("longform_ratio_pct") != ""
            else ""
        )
        top3_str = (
            f"{row.get('top3_share_pct', '')}%"
            if row.get("top3_share_pct") != ""
            else ""
        )
        recom_str = str(row.get("recommendation", ""))

        print(
            f"{i:<5} "
            f"{row['genre'][:24]:<26} "
            f"{astatus_str:<11} "
            f"{score_str:<8} "
            f"{status_str:<14} "
            f"{viab_str:<10} "
            f"{long_str:<10} "
            f"{top3_str:<12} "
            f"{recom_str:<20}"
        )

    print("=" * 130)


def run_discovery_once(
    api_key: str, base_url: str, model: str, known_genres: list
) -> list:
    prompt = build_discovery_prompt(known_genres)
    raw = call_omniroute(base_url, api_key, model, prompt)
    return parse_llm_output(raw)


def analyze_monetization_batch(
    target_keys: list, bank_dict: dict, csv_path: str, api_key: str
):
    if not target_keys:
        print("✓ Semua genre di bank sudah memiliki nilai monetisasi!")
        return

    print(
        f"\n🔍 Menjalankan Analisis Monetisasi YouTube API ({len(target_keys)} genre)...\n"
    )

    success_count = 0

    for i, key in enumerate(target_keys, 1):
        genre_name = bank_dict[key]["genre"]
        print(
            f"[{i}/{len(target_keys)}] Menganalisis '{genre_name}'...",
            end=" ",
            flush=True,
        )

        metrics = analyze_genre_youtube(genre_name, api_key=api_key)

        if not metrics:
            print("❌ Tidak ada data YouTube ditemukan.")
            no_data_row = dict(bank_dict[key])
            no_data_row["analysis_status"] = "no_data"
            no_data_row["analyzed_at"] = datetime.date.today().isoformat()
            update_genre_row(key, no_data_row, csv_path, bank_dict)
            continue

        update_genre_row(key, metrics, csv_path, bank_dict)
        success_count += 1

        print(
            f"✓ Score: {metrics['monetization_score']} ({metrics['competition_status']} - {metrics['recommendation']})"
        )
        time.sleep(0.5)

    print(
        f"\n✅ Analisis Monetisasi Selesai! ({success_count}/{len(target_keys)} genre di-update)"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Build & maintain niche music genre bank via LLM + YouTube API Monetization Analysis"
    )

    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Jumlah run LLM discovery (default: 1)",
    )

    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM discovery, langsung analisis monetisasi genre di bank yang belum dinilai",
    )

    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help="Skip LLM discovery, update data monetisasi YouTube API untuk SEMUA genre di bank",
    )

    parser.add_argument(
        "--list",
        action="store_true",
        help="Tampilkan tabel genre bank yang tersimpan",
    )

    parser.add_argument(
        "--model",
        type=str,
        default=OMNIROUTE_MODEL,
        help=f"Model OmniRoute (default: {OMNIROUTE_MODEL})",
    )

    parser.add_argument(
        "--base-url",
        type=str,
        default=OMNIROUTE_BASE_URL,
        help=f"Base URL OmniRoute (default: {OMNIROUTE_BASE_URL})",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=GENRE_BANK_CSV,
        help=f"Path genre bank CSV (default: {GENRE_BANK_CSV})",
    )

    parser.add_argument(
        "--manual",
        type=str,
        default=None,
        metavar="GENRE",
        help="Tambahkan & analisis 1 genre secara manual (tanpa LLM discovery)",
    )

    args = parser.parse_args()

    if args.list:
        print_bank(args.output)
        return

    bank_dict = load_existing_bank(args.output)
    print(f"Genre bank saat ini: {len(bank_dict)} genre ({args.output})")

    # Manual genre injection (skip LLM discovery)
    if args.manual:
        manual_name = args.manual.strip()
        if not manual_name or is_placeholder_genre(manual_name):
            print(
                f"⚠ Nama genre manual tidak valid atau berupa placeholder: '{args.manual}'"
            )
        else:
            manual_key = normalize_genre_key(manual_name)
            if manual_key in bank_dict:
                print(f"✓ '{manual_name}' sudah ada di bank, lewati.")
            else:
                append_genre_row(manual_name, args.output, bank_dict)
                print(f"➕ Manual genre ditambahkan: '{manual_name}' (pending)")

    # Step 1: LLM Discovery (unless --no-llm, --reanalyze, or --manual)
    if not args.no_llm and not args.reanalyze and not args.manual:
        omni_key = OMNIROUTE_API_KEY
        if not omni_key:
            print("ERROR: OMNIROUTE_API_KEY tidak ditemukan di .env")
            sys.exit(1)

        total_added = 0

        for run_i in range(1, args.count + 1):
            print(
                f"\n[LLM Discovery {run_i}/{args.count}] Memanggil OmniRoute ({args.model})..."
            )

            entries = []
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    known = list(b["genre"] for b in bank_dict.values())
                    entries = run_discovery_once(
                        omni_key, args.base_url, args.model, known
                    )
                    break
                except Exception as e:
                    print(f"    Attempt {attempt}/{MAX_ATTEMPTS} gagal: {e}")
                    if attempt < MAX_ATTEMPTS:
                        wait = 5 * attempt
                        print(f"    Retry dalam {wait}s...")
                        time.sleep(wait)
                    else:
                        print("    Skip run ini.")

            if not entries:
                print("    Tidak ada genre ter-parse dari jawaban LLM.")
                continue

            added = 0
            duplicates = 0
            seen_in_run = set()

            for entry in entries:
                key = normalize_genre_key(entry["genre"])
                if key in bank_dict or key in seen_in_run:
                    duplicates += 1
                    continue
                seen_in_run.add(key)
                append_genre_row(entry["genre"], args.output, bank_dict)
                added += 1

            total_added += added
            print(f"    Dari LLM        : {len(entries)} genre")
            print(f"    Duplikat/skip   : {duplicates}")
            print(f"    Genre baru masuk: {added}")

            if run_i < args.count:
                time.sleep(DELAY_BETWEEN_RUNS)

        print(f"\n✓ LLM Discovery Selesai! (+{total_added} genre baru)")

    # Step 2: YouTube API Monetization Analysis
    yt_key = YOUTUBE_API_KEY
    if not yt_key:
        print(
            "\n⚠ YOUTUBE_API_KEY tidak ditemukan di .env. Skip Analisis Monetisasi YouTube."
        )
        print(f"✓ Hasil genre tersimpan di: {args.output}")
        return

    if args.reanalyze:
        target_keys = list(bank_dict.keys())
    else:
        target_keys = [
            k
            for k, row in bank_dict.items()
            if row.get("analysis_status", "pending") == "pending"
        ]

    analyze_monetization_batch(target_keys, bank_dict, args.output, yt_key)


if __name__ == "__main__":
    main()
