"""
Cari channel YouTube dengan VIEWS PALING STABIL berdasarkan keyword genre musik.

Logika:
1. Search video pakai keyword genre -> dapat kandidat channel
2. Filter channel dengan subscriber di bawah MAX_SUBSCRIBERS
3. Untuk tiap channel yang lolos, ambil N video TERBARU (dari uploads playlist)
4. Hitung mean, std dev, dan Coefficient of Variation (CV) dari views video2 itu
5. CV rendah = views konsisten/stabil. CV tinggi = views naik-turun drastis (viral sesekali).
6. Urutkan channel dari paling stabil ke paling tidak stabil.

Cara pakai:
    pip install google-api-python-client
    python stable_channel_finder.py
"""

import datetime
import os
import statistics
from googleapiclient.discovery import build
from dotenv import load_dotenv
 
load_dotenv()  # baca file .env di folder yang sama
 
# ==== KONFIGURASI ====
API_KEY = os.getenv("YOUTUBE_API_KEY")
GENRE_KEYWORD = "soul funk"       # ganti sesuai genre yang dicari
NUM_CANDIDATE_VIDEOS = 50         # jumlah video awal buat cari kandidat channel
VIDEOS_PER_CHANNEL = 12           # jumlah video TERBARU per channel yang dianalisis
MIN_VIDEOS_REQUIRED = 5           # channel dengan video < ini dilewati (data kurang cukup)
MIN_AVG_VIEWS = 1000              # filter channel kekecilan (opsional, set 0 untuk nonaktifkan)
MAX_SUBSCRIBERS = 20000           # hanya channel dengan subscriber DI BAWAH angka ini

# PERINGATAN KUOTA:
# Mode "video terbaru" pakai channels.list + playlistItems.list = murah (~2 unit/channel).
# Ditambah channels.list untuk cek subscriber (~1 unit/channel).
# Kuota harian gratis = 10.000 unit -> sangat aman dijalankan berkali-kali.


def search_candidate_channels(youtube, query, max_results):
    """Cari video sesuai keyword, kembalikan daftar channel_id unik."""
    response = youtube.search().list(
        part="snippet",
        q=query,
        type="video",
        order="relevance",
        maxResults=max_results,
    ).execute()

    channels = {}
    for item in response.get("items", []):
        cid = item["snippet"]["channelId"]
        cname = item["snippet"]["channelTitle"]
        channels[cid] = cname
    return channels


def get_channel_stats(youtube, channel_id):
    """Ambil subscriberCount channel. Return None kalau data disembunyikan/tidak ada."""
    response = youtube.channels().list(
        part="statistics",
        id=channel_id,
    ).execute()
    items = response.get("items", [])
    if not items:
        return None
    stats = items[0]["statistics"]
    if stats.get("hiddenSubscriberCount", False):
        return None
    return int(stats.get("subscriberCount", 0))


def get_uploads_playlist_id(youtube, channel_id):
    """Tiap channel punya 'uploads playlist' otomatis, isinya semua video mereka."""
    response = youtube.channels().list(
        part="contentDetails",
        id=channel_id,
    ).execute()
    items = response.get("items", [])
    if not items:
        return None
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def get_recent_video_ids(youtube, playlist_id, max_results):
    """Ambil video TERBARU (bukan terpopuler) milik sebuah channel."""
    response = youtube.playlistItems().list(
        part="contentDetails",
        playlistId=playlist_id,
        maxResults=max_results,
    ).execute()
    return [item["contentDetails"]["videoId"] for item in response.get("items", [])]


def get_video_view_counts(youtube, video_ids):
    """Ambil viewCount untuk sekumpulan video (batch max 50 id per call)."""
    views = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        response = youtube.videos().list(
            part="statistics",
            id=",".join(batch),
        ).execute()
        for item in response.get("items", []):
            v = int(item["statistics"].get("viewCount", 0))
            views.append(v)
    return views


def analyze_channel_stability(views):
    """Hitung mean, std dev, dan coefficient of variation dari daftar views."""
    if len(views) < 2:
        return None
    mean_v = statistics.mean(views)
    std_v = statistics.stdev(views)
    cv = (std_v / mean_v) if mean_v > 0 else float("inf")
    return {
        "mean": mean_v,
        "std": std_v,
        "cv": cv,
        "min": min(views),
        "max": max(views),
        "n": len(views),
    }


def main():
    youtube = build("youtube", "v3", developerKey=API_KEY)

    print(f"Mencari kandidat channel untuk genre: '{GENRE_KEYWORD}'...\n")
    candidates = search_candidate_channels(youtube, GENRE_KEYWORD, NUM_CANDIDATE_VIDEOS)
    print(f"Ditemukan {len(candidates)} channel kandidat. Menganalisis stabilitas views...\n")

    results = []

    for cid, cname in candidates.items():
        subs = get_channel_stats(youtube, cid)
        if subs is None or subs >= MAX_SUBSCRIBERS:
            continue

        playlist_id = get_uploads_playlist_id(youtube, cid)
        if not playlist_id:
            continue

        video_ids = get_recent_video_ids(youtube, playlist_id, VIDEOS_PER_CHANNEL)
        if len(video_ids) < MIN_VIDEOS_REQUIRED:
            continue

        views = get_video_view_counts(youtube, video_ids)
        stats = analyze_channel_stability(views)
        if stats is None:
            continue
        if stats["mean"] < MIN_AVG_VIEWS:
            continue

        results.append({
            "channel": cname,
            "channel_id": cid,
            "channel_url": f"https://www.youtube.com/channel/{cid}",
            "subscribers": subs,
            **stats,
        })

    # Urutkan dari CV terendah (paling stabil) ke tertinggi
    results.sort(key=lambda r: r["cv"])

    print(f"{'#':>3} {'Channel':30} {'Subs':>8} {'Avg Views':>12} {'CV':>8} {'Min':>10} {'Max':>10} {'#Video':>7}")
    print("-" * 105)
    for i, r in enumerate(results, start=1):
        print(f"{i:>3} {r['channel'][:28]:30} {r['subscribers']:>8,} {r['mean']:>12,.0f} {r['cv']:>8.2f} "
              f"{r['min']:>10,} {r['max']:>10,} {r['n']:>7}")
        print(f"    -> {r['channel_url']}")

    print("\nCatatan:")
    print("- CV (Coefficient of Variation) makin RENDAH = views makin stabil/konsisten.")
    print("- CV di atas ~1.0 biasanya menandakan ada video yang jauh lebih viral dari yang lain.")
    print("- Channel paling atas di daftar = kandidat paling 'stabil' sesuai kriteria kamu.")
    print(f"- Hanya channel dengan subscriber < {MAX_SUBSCRIBERS:,} yang ditampilkan.")
    print("- Analisis stabilitas di sini berbasis video TERBARU channel.")


if __name__ == "__main__":
    main()