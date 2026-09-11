"""
generate_music_final.py
========================
Menggabungkan file audio (.mp3) berdasarkan sequence urutan di urutan.json
menggunakan FFmpeg concat demuxer.

Hasil akhir disimpan di:
    output/[genre]/[channel]/final/[index]/music.mp3

Cara pakai:
    python generate_music_final.py --genre ambient_dub --channel "Moonleaf Audio"

    python generate_music_final.py --genre ambient_dub --channel "Moonleaf Audio" --overwrite

    python generate_music_final.py --genre ambient_dub --channel "Moonleaf Audio" --indices "1,3,5"
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = "output"
GENRE_FOLDER = "ambient_dub"
CHANNEL_FOLDER = "Moonleaf Audio"
URUTAN_FILENAME = "urutan.json"
AUDIO_DIR_NAME = "audio"
FINAL_DIR_NAME = "final"
MUSIC_FILENAME = "music.mp3"
TRACKLIST_FILENAME = "tracklist.txt"
BRIEF_FILENAME = "brief.txt"

OMNIROUTE_API_KEY = os.getenv("OMNIROUTE_API_KEY")
OMNIROUTE_BASE_URL = "http://localhost:20128/v1"
OMNIROUTE_MODEL = "combo-analisis"


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


def check_ffmpeg() -> str:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        print("❌ FFmpeg tidak ditemukan di PATH sistem.")
        print("   Pastikan FFmpeg sudah terinstall (misal: brew install ffmpeg).")
        sys.exit(1)
    return ffmpeg_path


def load_urutan(channel_dir: str) -> dict:
    urutan_path = os.path.join(channel_dir, URUTAN_FILENAME)
    if not os.path.isfile(urutan_path):
        raise FileNotFoundError(f"{URUTAN_FILENAME} tidak ditemukan di {channel_dir}")

    with open(urutan_path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_channel_dirs(output_dir: str, genre_folder: Optional[str] = None) -> List[str]:
    channel_dirs = []

    if not os.path.isdir(output_dir):
        return channel_dirs

    if genre_folder:
        genre_path = os.path.join(output_dir, genre_folder)
        if not os.path.isdir(genre_path):
            return channel_dirs
        genres = [genre_folder]
    else:
        genres = [
            d
            for d in os.listdir(output_dir)
            if os.path.isdir(os.path.join(output_dir, d))
        ]

    for g in sorted(genres):
        g_path = os.path.join(output_dir, g)
        for c in sorted(os.listdir(g_path)):
            c_path = os.path.join(g_path, c)
            if os.path.isdir(c_path):
                urutan_file = os.path.join(c_path, URUTAN_FILENAME)
                if os.path.isfile(urutan_file):
                    channel_dirs.append(c_path)

    return channel_dirs


def concat_audio_ffmpeg(
    track_paths: List[str], output_music_path: str, ffmpeg_bin: str
):
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as tmp:
        tmp_path = tmp.name
        for p in track_paths:
            abs_p = os.path.abspath(p)
            escaped = abs_p.replace("'", "'\\''")
            tmp.write(f"file '{escaped}'\n")

    try:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            tmp_path,
            "-c",
            "copy",
            output_music_path,
        ]

        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        if result.returncode != 0:
            # Fallback jika -c copy gagal (misal beda sample rate / bit rate), lakukan re-encode
            cmd_reencode = [
                ffmpeg_bin,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                tmp_path,
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                output_music_path,
            ]
            res2 = subprocess.run(
                cmd_reencode,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if res2.returncode != 0:
                raise RuntimeError(
                    f"FFmpeg error: {res2.stderr.strip() or result.stderr.strip()}"
                )
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def load_json_safe(filepath: str) -> Optional[dict]:
    """Load JSON file with error handling, return None if failed"""
    try:
        if os.path.isfile(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def call_omniroute_title(prompt: str, base_url: str, api_key: str, model: str, timeout: int = 30) -> Optional[str]:
    """Call OmniRoute API to generate YouTube title"""
    if not api_key:
        return None
    
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": 100,
    }
    
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "").strip()
            if content:
                return content
    except Exception:
        pass
    
    return None


def generate_title_ai(channel_dir: str, seq_data: dict, api_key: str, base_url: str, model: str) -> str:
    """Generate YouTube title using AI or fallback to template"""
    
    # Load metadata
    meta_channel = load_json_safe(os.path.join(channel_dir, "meta_channel.json"))
    meta_populer = load_json_safe(os.path.join(channel_dir, "meta_populer.json"))
    variation_music = load_json_safe(os.path.join(channel_dir, "variation_music.json"))
    
    channel_name = meta_channel.get("snippet", {}).get("title", "Unknown Channel") if meta_channel else "Unknown Channel"
    channel_desc_snippet = meta_channel.get("snippet", {}).get("description", "")[:200] if meta_channel else ""
    
    # Extract genre/mood
    genre = variation_music.get("musical_dna", {}).get("genre", "Music") if variation_music else "Music"
    subgenre = variation_music.get("musical_dna", {}).get("subgenre", "") if variation_music else ""
    mood = variation_music.get("musical_dna", {}).get("mood", "Chill") if variation_music else "Chill"
    energy = variation_music.get("musical_dna", {}).get("energy", "Low") if variation_music else "Low"
    
    duration_formatted = seq_data.get("duration_formatted", "60:00")
    track_count = seq_data.get("total_tracks", 15)
    
    # Get example titles from popular videos
    example_titles = []
    if meta_populer and isinstance(meta_populer, list):
        for video in meta_populer[:3]:
            title = video.get("snippet", {}).get("title", "")
            if title:
                example_titles.append(title)
    
    # Build AI prompt
    prompt = f"""You are a YouTube SEO expert specializing in music channels. Generate a catchy, SEO-optimized YouTube video title for this music mix.

CHANNEL CONTEXT:
- Channel Name: {channel_name}
- Channel Style: {channel_desc_snippet}

MIX DETAILS:
- Genre: {genre}
- Subgenre: {subgenre}
- Mood: {mood}
- Energy: {energy}
- Duration: {duration_formatted}
- Track Count: {track_count}

POPULAR TITLE EXAMPLES FROM THIS CHANNEL:
{chr(10).join(f'{i+1}. "{t}"' for i, t in enumerate(example_titles[:2]))}

REQUIREMENTS:
1. Length: 70-80 characters maximum (STRICT)
2. Include 1-2 tasteful emojis (e.g., 🌙, 🎧, ✨)
3. Mention genre/mood prominently
4. Mention use case (e.g., "for Late Night Sessions", "Study Music", "Chill Vibes")
5. SEO keywords at the beginning
6. Match the channel's existing tone and style
7. Make it compelling and click-worthy

OUTPUT:
Return ONLY the title text. No quotes, no explanation, no extra text."""
    
    # Try AI generation
    title = call_omniroute_title(prompt, base_url, api_key, model)
    
    # Fallback to template if AI failed
    if not title or len(title) > 100 or len(title) < 30:
        # Manual template fallback
        use_case = "Late Night Sessions" if "night" in mood.lower() or "dark" in mood.lower() else "Chill Sessions"
        title = f"{genre} Mix 🌙 {mood} Vibes for {use_case} | {duration_formatted}"
    
    # Trim if too long
    if len(title) > 100:
        title = title[:97] + "..."
    
    return title


def build_description_from_template(channel_dir: str, seq_data: dict, tracklist_path: str) -> str:
    """Build YouTube description from template based on popular videos"""
    
    meta_channel = load_json_safe(os.path.join(channel_dir, "meta_channel.json"))
    meta_populer = load_json_safe(os.path.join(channel_dir, "meta_populer.json"))
    variation_music = load_json_safe(os.path.join(channel_dir, "variation_music.json"))
    
    # Channel intro
    channel_desc = ""
    if meta_channel:
        channel_desc = meta_channel.get("snippet", {}).get("description", "")
        # Take first paragraph only (before first double newline)
        if "\n\n" in channel_desc:
            channel_desc = channel_desc.split("\n\n")[0]
    
    # Extract genre features from variation_music
    features = []
    if variation_music:
        dna = variation_music.get("musical_dna", {})
        if dna.get("genre"):
            features.append(dna["genre"])
        if dna.get("subgenre"):
            features.append(dna["subgenre"])
        if dna.get("mood"):
            features.append(dna["mood"])
    
    if not features:
        features = ["Deep Bass", "Chill Vibes", "Atmospheric Soundscapes"]
    
    # Use cases (from popular video patterns)
    use_cases = [
        "Relaxing after a long day",
        "Night drives & city lights",
        "Gaming & deep focus",
        "Studying & creative work",
        "Meditation & mindfulness",
        "Late-night listening",
        "Atmospheric background music"
    ]
    
    # Load tracklist
    tracklist = ""
    if os.path.isfile(tracklist_path):
        with open(tracklist_path, "r", encoding="utf-8") as f:
            tracklist = f.read().strip()
    
    # Get tags from meta_channel
    tags_text = ""
    if meta_channel:
        keywords = meta_channel.get("brandingSettings", {}).get("channel", {}).get("keywords", "")
        if keywords:
            # Parse keywords (space-separated quoted strings)
            tags_list = re.findall(r'"([^"]+)"', keywords)
            if tags_list:
                tags_text = " ".join(f"#{tag.replace(' ', '')}" for tag in tags_list[:15])
    
    # Build description
    desc = f"""{channel_desc}

🎧 THIS MIX FEATURES:
{chr(10).join(f"• {feat}" for feat in features[:8])}

🌌 PERFECT FOR:
{chr(10).join(f"• {uc}" for uc in use_cases)}

📀 TRACKLIST:
{tracklist}

📀 ABOUT THIS CONTENT:
Every track featured on this channel is 100% original, commercially licensed AI-generated music, professionally produced for smooth, high-quality listening. Every visual is custom-crafted to complement the music, creating a seamless audio-visual experience.

🌙 If you're enjoying the vibe, don't forget to Like, Subscribe, and turn on notifications for more mixes every week.

💬 Tell us where you're listening from and which vibe you'd like to hear next!

{tags_text}"""
    
    return desc


def extract_tags(channel_dir: str) -> str:
    """Extract and combine tags from meta_channel and variation_music"""
    
    meta_channel = load_json_safe(os.path.join(channel_dir, "meta_channel.json"))
    variation_music = load_json_safe(os.path.join(channel_dir, "variation_music.json"))
    
    tags = []
    
    # From meta_channel keywords
    if meta_channel:
        keywords = meta_channel.get("brandingSettings", {}).get("channel", {}).get("keywords", "")
        if keywords:
            tags_list = re.findall(r'"([^"]+)"', keywords)
            tags.extend(tags_list[:20])
    
    # From variation_music
    if variation_music:
        dna = variation_music.get("musical_dna", {})
        for key in ["genre", "subgenre", "mood"]:
            val = dna.get(key)
            if val and val not in tags:
                tags.append(val)
    
    return ", ".join(tags[:30])


def generate_brief(channel_dir: str, seq_data: dict, brief_path: str, tracklist_path: str, api_key: str, base_url: str, model: str):
    """Generate YouTube brief.txt with title, description, tags, metadata"""
    
    variation_music = load_json_safe(os.path.join(channel_dir, "variation_music.json"))
    meta_channel = load_json_safe(os.path.join(channel_dir, "meta_channel.json"))
    
    # Generate title (AI)
    title = generate_title_ai(channel_dir, seq_data, api_key, base_url, model)
    
    # Build description
    description = build_description_from_template(channel_dir, seq_data, tracklist_path)
    
    # Extract tags
    tags = extract_tags(channel_dir)
    
    # Metadata
    duration_formatted = seq_data.get("duration_formatted", "N/A")
    duration_seconds = seq_data.get("actual_duration_seconds", 0)
    track_count = seq_data.get("total_tracks", 0)
    
    genre = "N/A"
    subgenre = "N/A"
    mood = "N/A"
    energy = "N/A"
    bpm_range = "N/A"
    
    if variation_music:
        dna = variation_music.get("musical_dna", {})
        genre = dna.get("genre", "N/A")
        subgenre = dna.get("subgenre", "N/A")
        mood = dna.get("mood", "N/A")
        energy = dna.get("energy", "N/A")
        tempo = dna.get("tempo_bpm", "")
        if tempo:
            bpm_range = tempo
    
    channel_name = meta_channel.get("snippet", {}).get("title", "Unknown Channel") if meta_channel else "Unknown Channel"
    seq_index = seq_data.get("index", "N/A")
    
    # Get current timestamp
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    
    # Build brief.txt
    brief_content = f"""=== YOUTUBE VIDEO BRIEF ===
Generated: {now}
Channel: {channel_name}
Sequence: #{seq_index}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📺 TITLE (70-100 chars)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{title}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📝 DESCRIPTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{description}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏷️ TAGS (comma-separated)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{tags}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 METADATA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Duration: {duration_formatted} ({int(duration_seconds)} seconds)
Track Count: {track_count}
Genre: {genre}
Subgenre: {subgenre}
Mood: {mood}
Energy: {energy}
BPM Range: {bpm_range}

=== END BRIEF ===
"""
    
    # Write to file
    with open(brief_path, "w", encoding="utf-8") as f:
        f.write(brief_content)



def process_channel(
    channel_dir: str,
    ffmpeg_bin: str,
    overwrite: bool,
    selected_indices: Optional[List[int]] = None,
):
    rel_path = os.path.relpath(channel_dir)
    print(f"\n🎵 Memproses channel: {rel_path}")

    try:
        data = load_urutan(channel_dir)
    except Exception as e:
        print(f"  ❌ Gagal membaca {URUTAN_FILENAME}: {e}")
        return

    sequences = data.get("sequences", [])
    if not sequences:
        print(f"  ❌ Tidak ada sequences di {URUTAN_FILENAME}.")
        return

    audio_dir = os.path.join(channel_dir, AUDIO_DIR_NAME)
    if not os.path.isdir(audio_dir):
        print(f"  ❌ Folder audio tidak ditemukan: {audio_dir}")
        return

    total_done, total_skipped, total_failed = 0, 0, 0

    for seq in sequences:
        seq_idx = seq.get("index")
        if seq_idx is None:
            continue

        if selected_indices is not None and seq_idx not in selected_indices:
            continue

        tracks = seq.get("tracks", [])
        if not tracks:
            print(f"  - [{seq_idx}] SKIP: tidak ada tracks.")
            total_failed += 1
            continue

        final_seq_dir = os.path.join(channel_dir, FINAL_DIR_NAME, str(seq_idx))
        os.makedirs(final_seq_dir, exist_ok=True)
        final_music_path = os.path.join(final_seq_dir, MUSIC_FILENAME)

        if os.path.isfile(final_music_path) and not overwrite:
            print(
                f"  - [{seq_idx}] SKIP: music.mp3 sudah ada (pakai --overwrite untuk timpa)"
            )
            total_skipped += 1
            continue

        # Cek dan kumpulkan path track
        track_paths = []
        missing_tracks = []

        for tr in tracks:
            fname = tr.get("filename")
            if not fname:
                continue
            fpath = os.path.join(audio_dir, fname)
            if os.path.isfile(fpath):
                track_paths.append(fpath)
            else:
                missing_tracks.append(fname)

        if missing_tracks:
            print(
                f"  - [{seq_idx}] GAGAL: {len(missing_tracks)} file audio tidak ditemukan di {audio_dir} "
                f"(misal: {missing_tracks[0]})"
            )
            total_failed += 1
            continue

        print(
            f"  - [{seq_idx}] Penggabungan {len(track_paths)} track audio...",
            end=" ",
            flush=True,
        )

        try:
            concat_audio_ffmpeg(track_paths, final_music_path, ffmpeg_bin)
            
            # Buat tracklist.txt
            tracklist_path = os.path.join(final_seq_dir, TRACKLIST_FILENAME)
            with open(tracklist_path, "w", encoding="utf-8") as f:
                for tr in tracks:
                    start_time = tr.get("start_time", "")
                    filename = tr.get("filename", "")
                    track_name = filename.rsplit(".", 1)[0] if "." in filename else filename
                    f.write(f"{start_time} - {track_name}\n")
            
            # Generate brief.txt
            brief_path = os.path.join(final_seq_dir, BRIEF_FILENAME)
            try:
                generate_brief(
                    channel_dir=channel_dir,
                    seq_data=seq,
                    brief_path=brief_path,
                    tracklist_path=tracklist_path,
                    api_key=OMNIROUTE_API_KEY,
                    base_url=OMNIROUTE_BASE_URL,
                    model=OMNIROUTE_MODEL,
                )
            except Exception as e:
                print(f"\n    ⚠ Brief generation gagal: {e}")
            
            # Copy image.png
            image_src = os.path.join(channel_dir, "image_references", f"{seq_idx}.png")
            image_dst = os.path.join(final_seq_dir, "image.png")
            
            if os.path.isfile(image_src):
                if not os.path.isfile(image_dst) or overwrite:
                    try:
                        shutil.copy2(image_src, image_dst)
                    except Exception as e:
                        print(f"\n    ⚠ Image copy gagal: {e}")
            else:
                print(f"\n    ⚠ Image not found: image_references/{seq_idx}.png")
            
            print("OK")
            total_done += 1
        except Exception as e:
            print(f"GAGAL ({e})")
            total_failed += 1

    print("=== Ringkasan Channel ===")
    print(f"Berhasil : {total_done}")
    print(f"Dilewati : {total_skipped}")
    print(f"Gagal    : {total_failed}")


def main():
    parser = argparse.ArgumentParser(
        description="Menggabungkan audio sequence dari urutan.json menjadi final/[index]/music.mp3"
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
        help="Nama folder channel spesifik (opsional, butuh --genre)",
    )
    parser.add_argument(
        "--indices",
        default=None,
        help="Filter index sequence (contoh: 1 atau 1,3,5-10)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Timpa file music.mp3 jika sudah ada",
    )

    args = parser.parse_args()
    ffmpeg_bin = check_ffmpeg()

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

    if args.channel:
        channel_dir = os.path.join(args.output_dir, args.genre, args.channel)
        if not os.path.isdir(channel_dir):
            print(f"❌ Folder channel tidak ditemukan: {channel_dir}")
            sys.exit(1)
        channel_dirs = [channel_dir]
    else:
        channel_dirs = find_channel_dirs(args.output_dir, args.genre)

    if not channel_dirs:
        print("❌ Tidak ada folder channel yang memiliki urutan.json.")
        sys.exit(1)

    print(f"🔍 Ditemukan {len(channel_dirs)} channel dengan {URUTAN_FILENAME}.")
    for c_dir in channel_dirs:
        process_channel(c_dir, ffmpeg_bin, args.overwrite, selected_indices)


if __name__ == "__main__":
    main()
