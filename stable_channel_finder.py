"""
Cari channel YouTube dengan pola views STABIL berdasarkan keyword genre musik,
menggunakan rule manual (bukan cuma statistik CV).

RULE SET:
---------
Pre-filter (sebelum tarik data berat, hemat kuota):
  - Exclude channel yang namanya mengandung "- Topic" (auto-generated, bukan channel asli)
  - Exclude channel dengan subscriber >= MAX_SUBSCRIBERS atau hidden

Rule 1 (List A = video terbaru, urut upload) -- MEMPENGARUHI PASS/FAIL:
  - RULE1_EXEMPT_NEWEST (3) video paling baru: DI-SKIP dari perhitungan
  - Video ke-4 dst: dicek SATU PER SATU
  - Video dengan views < MIN_VIEWS_FLOOR dianggap "low"
  - Kalau jumlah video "low" > RULE1_MAX_LOW_VIEWS -> FAIL Rule 1

Rule 2 (List B = video terpopuler, tab "Popular") -- MEMPENGARUHI PASS/FAIL:
  - Video umur <= RULE2_MONTHS (6 bulan): aman
  - Video umur RULE2_MONTHS < umur <= RULE2_OLD_EXEMPT_MONTHS (6-12 bulan):
    dihitung "tua tapi exempt"
  - Video umur > RULE2_OLD_EXEMPT_MONTHS (>12 bulan): otomatis FAIL
  - Total video "tua tapi exempt" > RULE2_MAX_OLD_EXEMPT (3): FAIL

Rule 3 (List A, video paling baru) -- MEMPENGARUHI PASS/FAIL:
  - Video terbaru wajib di-upload dalam RULE3_MAX_DAYS_SINCE_LAST_UPLOAD hari terakhir

Info tambahan:
  - Rata-rata interval upload
  - Negara channel
  - Jam upload video terpopuler

THUMBNAIL:
  - Channel PASS akan mengambil 10 thumbnail dari video terpopuler List B
  - Semua thumbnail digabung menjadi satu gambar grid 2x5
  - Disimpan sebagai:
      output/{genre}/{nama_channel}/thumbnails_10.jpg

META LENGKAP (BARU):
  - Channel PASS akan mengambil metadata LENGKAP channel (snippet,
    statistics, contentDetails, brandingSettings, status, topicDetails)
    dan menyimpannya sebagai:
      output/{genre}/{nama_channel}/meta_channel.json
  - Channel PASS juga mengambil metadata LENGKAP dari 5 video terpopuler
    (snippet, statistics, contentDetails, status, topicDetails,
    recordingDetails, liveStreamingDetails) dan menyimpannya sebagai:
      output/{genre}/{nama_channel}/meta_populer.json

CSV:
  - Nama channel di-sanitize agar aman
  - channel_url tetap menggunakan Channel ID asli

Cara pakai:
    pip install google-api-python-client python-dotenv pillow
    isi .env:
        YOUTUBE_API_KEY=xxx

    python stable_channel_finder.py
"""

import csv
import datetime
import json
import os
import re
import statistics
import urllib.request

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv
from PIL import Image, ImageOps, ImageDraw

load_dotenv()


# ============================================================
# KONFIGURASI
# ============================================================

API_KEY = os.getenv("YOUTUBE_API_KEY")

GENRE_KEYWORD = "Forest Psytrance"

NUM_CANDIDATE_VIDEOS = 25

POPULAR_WITHIN_DAYS = 30

MAX_SUBSCRIBERS = 20000

MIN_VIDEOS_REQUIRED = 15

SAMPLE_SIZE = 15


# ============================================================
# OUTPUT
# ============================================================

CSV_OUTPUT_DIR = "output"


# ============================================================
# RULE 1
# ============================================================

RULE1_EXEMPT_NEWEST = 3

MIN_VIEWS_FLOOR = 1000

RULE1_MAX_LOW_VIEWS = 2


# ============================================================
# RULE 2
# ============================================================

RULE2_MONTHS = 6

RULE2_OLD_EXEMPT_MONTHS = 12

RULE2_MAX_OLD_EXEMPT = 3


# ============================================================
# FILTER SHORTS
# ============================================================

EXCLUDE_SHORTS = True

SHORT_MAX_SECONDS = 60

LIST_B_MIN_DURATION_BUCKET = "medium"

LIST_A_MAX_PAGES = 4


# ============================================================
# INFO UPLOAD
# ============================================================

MAX_UPLOAD_INTERVAL_DAYS = 30


# ============================================================
# THUMBNAIL
# ============================================================

THUMBNAIL_COUNT = 10

THUMBNAIL_COLUMNS = 5

THUMBNAIL_ROWS = 2

THUMBNAIL_WIDTH = 640

THUMBNAIL_HEIGHT = 360

THUMBNAIL_GAP = 10

THUMBNAIL_BACKGROUND = (30, 30, 30)


# ============================================================
# META LENGKAP (BARU)
# ============================================================

META_CHANNEL_FILENAME = "meta_channel.json"

META_POPULER_FILENAME = "meta_populer.json"

META_POPULER_COUNT = 5

CHANNEL_META_PARTS = (
    "snippet,statistics,contentDetails," "brandingSettings,status,topicDetails"
)

VIDEO_META_PARTS = (
    "snippet,statistics,contentDetails,status,"
    "topicDetails,recordingDetails,liveStreamingDetails"
)


# ============================================================
# TIMEZONE
# ============================================================

WIB_OFFSET_HOURS = 7


# ============================================================
# HELPER: TANGGAL
# ============================================================


def get_published_after(days=7):
    """Tanggal N hari lalu dalam format RFC 3339 UTC."""

    dt = datetime.datetime.utcnow() - datetime.timedelta(days=days)

    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(dt_str):
    """Parse timestamp RFC3339 menjadi datetime UTC naive."""

    return datetime.datetime.strptime(
        dt_str,
        "%Y-%m-%dT%H:%M:%SZ",
    )


def age_in_months(published_at, now=None):
    now = now or datetime.datetime.utcnow()

    dt = parse_iso(published_at)

    return (now - dt).days / 30.44


def upload_hour_wib(published_at_str):
    """
    Konversi jam upload UTC menjadi WIB.
    """

    if not published_at_str:
        return "N/A"

    dt_utc = parse_iso(published_at_str)

    dt_wib = dt_utc + datetime.timedelta(hours=WIB_OFFSET_HOURS)

    return dt_wib.strftime("%H:%M WIB")


def parse_duration_to_seconds(duration_str):
    """
    Parse durasi ISO 8601 menjadi detik.
    """

    if not duration_str:
        return 0

    match = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
        duration_str,
    )

    if not match:
        return 0

    hours, minutes, seconds = (int(x) if x else 0 for x in match.groups())

    return hours * 3600 + minutes * 60 + seconds


# ============================================================
# HELPER: SANITIZE
# ============================================================


def sanitize_filename(name):
    """
    Sanitasi nama agar aman digunakan sebagai:
      - nama folder
      - nama file
      - nilai channel di CSV

    Karakter terlarang:
      / \\ : * ? " < > |
    """

    if not name:
        return "unknown_channel"

    name = str(name).strip()

    # Hapus karakter filesystem terlarang
    name = re.sub(
        r'[\\/*?:"<>|]',
        "",
        name,
    )

    # Normalisasi whitespace
    name = re.sub(
        r"\s+",
        " ",
        name,
    ).strip()

    # Hindari nama kosong
    if not name:
        return "unknown_channel"

    # Maksimal 100 karakter
    return name[:100]


def sanitize_genre_name(name):
    """
    Sanitasi genre untuk nama folder.
    """

    if not name:
        return "unknown_genre"

    return (
        re.sub(
            r"[^a-zA-Z0-9]+",
            "_",
            name,
        )
        .strip("_")
        .lower()
    )


# ============================================================
# THUMBNAIL URL
# ============================================================


def get_best_thumbnail_url(thumbnails):
    """
    Ambil thumbnail dengan resolusi terbaik.
    """

    if not thumbnails:
        return None

    for key in (
        "maxres",
        "standard",
        "high",
        "medium",
        "default",
    ):
        if key in thumbnails and thumbnails[key].get("url"):
            return thumbnails[key]["url"]

    return None


# ============================================================
# DOWNLOAD THUMBNAIL
# ============================================================


def download_thumbnail(video, filepath):
    """
    Download thumbnail satu video.
    """

    if not video:
        return False

    thumb_url = get_best_thumbnail_url(video.get("thumbnails", {}))

    if not thumb_url:
        return False

    try:
        urllib.request.urlretrieve(
            thumb_url,
            filepath,
        )

        return True

    except Exception as e:
        print(f"  ⚠️ Gagal download thumbnail: {e}")

        return False


# ============================================================
# CREATE CONTACT SHEET
# ============================================================


def create_thumbnail_contact_sheet(
    videos,
    output_dir,
    genre_keyword,
    channel_name,
):
    """
    Download maksimal THUMBNAIL_COUNT thumbnail
    video terpopuler lalu gabungkan menjadi satu
    gambar contact sheet.

    Layout:
        01 | 02 | 03 | 04 | 05
        06 | 07 | 08 | 09 | 10

    Output:
        output/{genre}/{channel}/thumbnails_10.jpg
    """

    if not videos:
        return None

    # Ambil maksimal 10 video
    selected_videos = videos[:THUMBNAIL_COUNT]

    safe_genre = sanitize_genre_name(genre_keyword)

    safe_channel = sanitize_filename(channel_name)

    folder = os.path.join(
        output_dir,
        safe_genre,
        safe_channel,
    )

    os.makedirs(
        folder,
        exist_ok=True,
    )

    temp_dir = os.path.join(
        folder,
        "_thumbnail_temp",
    )

    os.makedirs(
        temp_dir,
        exist_ok=True,
    )

    downloaded_images = []

    print(
        f"  🖼️ Mengambil {len(selected_videos)} thumbnail "
        f"terpopuler untuk '{safe_channel}'..."
    )

    for index, video in enumerate(
        selected_videos,
        start=1,
    ):

        video_id = video.get(
            "video_id",
            f"video_{index}",
        )

        temp_path = os.path.join(
            temp_dir,
            f"{index:02d}_{video_id}.jpg",
        )

        success = download_thumbnail(
            video,
            temp_path,
        )

        if not success:
            continue

        try:
            image = Image.open(temp_path).convert("RGB")

            downloaded_images.append(
                {
                    "index": index,
                    "image": image,
                    "video": video,
                }
            )

        except Exception as e:
            print(f"  ⚠️ Gagal membaca thumbnail #{index}: {e}")

    if not downloaded_images:
        print(
            f"  ⚠️ Tidak ada thumbnail yang berhasil "
            f"didownload untuk '{safe_channel}'"
        )

        return None

    # ========================================================
    # Buat canvas
    # ========================================================

    columns = THUMBNAIL_COLUMNS

    rows = THUMBNAIL_ROWS

    cell_width = THUMBNAIL_WIDTH

    cell_height = THUMBNAIL_HEIGHT

    gap = THUMBNAIL_GAP

    canvas_width = columns * cell_width + (columns + 1) * gap

    canvas_height = rows * cell_height + (rows + 1) * gap

    canvas = Image.new(
        "RGB",
        (
            canvas_width,
            canvas_height,
        ),
        THUMBNAIL_BACKGROUND,
    )

    # ========================================================
    # Tempel thumbnail
    # ========================================================

    for position, item in enumerate(downloaded_images):

        image = item["image"]

        # Crop agar ratio menjadi 16:9
        image = ImageOps.fit(
            image,
            (
                cell_width,
                cell_height,
            ),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

        row = position // columns

        column = position % columns

        x = gap + column * (cell_width + gap)

        y = gap + row * (cell_height + gap)

        canvas.paste(
            image,
            (
                x,
                y,
            ),
        )

        # Nomor thumbnail
        draw = ImageDraw.Draw(canvas)

        number_text = str(item["index"])

        draw.rectangle(
            (
                x + 8,
                y + 8,
                x + 45,
                y + 45,
            ),
            fill=(0, 0, 0),
        )

        draw.text(
            (
                x + 20,
                y + 15,
            ),
            number_text,
            fill=(255, 255, 255),
        )

    # ========================================================
    # Simpan contact sheet
    # ========================================================

    output_path = os.path.join(
        folder,
        "thumbnails_10.jpg",
    )

    try:

        canvas.save(
            output_path,
            "JPEG",
            quality=92,
            optimize=True,
        )

        print(f"  ✅ Contact sheet disimpan: {output_path}")

    except Exception as e:

        print(f"  ⚠️ Gagal menyimpan contact sheet: {e}")

        return None

    # ========================================================
    # Hapus file temporary
    # ========================================================

    try:

        for filename in os.listdir(temp_dir):

            filepath = os.path.join(
                temp_dir,
                filename,
            )

            try:
                os.remove(filepath)
            except Exception:
                pass

        try:
            os.rmdir(temp_dir)
        except Exception:
            pass

    except Exception:
        pass

    return output_path


# ============================================================
# JSON HELPER (BARU)
# ============================================================


def save_json(data, filepath):
    """
    Simpan `data` sebagai file JSON rapi (indent=2, UTF-8).
    Membuat folder tujuan otomatis jika belum ada.
    """

    try:

        os.makedirs(
            os.path.dirname(filepath),
            exist_ok=True,
        )

        with open(
            filepath,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

        return filepath

    except Exception as e:

        print(f"  ⚠️ Gagal menyimpan JSON ke {filepath}: {e}")

        return None


# ============================================================
# META CHANNEL LENGKAP (BARU)
# ============================================================


def get_full_channel_metadata(youtube, channel_id):
    """
    Ambil metadata LENGKAP satu channel:
    snippet, statistics, contentDetails,
    brandingSettings, status, topicDetails.

    Return raw dict item dari API (bukan hanya subset),
    atau None kalau gagal / tidak ditemukan.
    """

    try:

        response = (
            youtube.channels()
            .list(
                part=CHANNEL_META_PARTS,
                id=channel_id,
            )
            .execute()
        )

    except HttpError as e:

        print(f"  ⚠️ Gagal ambil meta_channel: {e}")

        return None

    items = response.get(
        "items",
        [],
    )

    if not items:
        return None

    return items[0]


# ============================================================
# META VIDEO LENGKAP (BARU)
# ============================================================


def get_full_video_metadata(youtube, video_ids):
    """
    Ambil metadata LENGKAP untuk daftar video_id:
    snippet, statistics, contentDetails, status,
    topicDetails, recordingDetails, liveStreamingDetails.

    Return list of raw dict item dari API, urutan
    TIDAK dijamin sama seperti video_ids (mengikuti
    urutan response API), jadi di-reorder sesuai input.
    """

    if not video_ids:
        return []

    items_by_id = {}

    try:

        for i in range(
            0,
            len(video_ids),
            50,
        ):

            batch = video_ids[i : i + 50]

            response = (
                youtube.videos()
                .list(
                    part=VIDEO_META_PARTS,
                    id=",".join(batch),
                )
                .execute()
            )

            for item in response.get(
                "items",
                [],
            ):

                items_by_id[item["id"]] = item

    except HttpError as e:

        print(f"  ⚠️ Gagal ambil meta_populer: {e}")

    # Kembalikan sesuai urutan video_ids asli (skip yang gagal ditemukan)
    return [items_by_id[vid] for vid in video_ids if vid in items_by_id]


# ============================================================
# TAHAP 0: CARI KANDIDAT
# ============================================================


def search_candidate_channels(
    youtube,
    query,
    max_results,
    days=7,
):
    """
    Cari video populer dalam N hari terakhir.
    """

    response = (
        youtube.search()
        .list(
            part="snippet",
            q=query,
            type="video",
            videoDuration="long",
            publishedAfter=get_published_after(days),
            order="viewCount",
            maxResults=max_results,
        )
        .execute()
    )

    channels = {}

    for item in response.get(
        "items",
        [],
    ):

        cid = item["snippet"]["channelId"]

        cname = item["snippet"]["channelTitle"]

        channels[cid] = cname

    return channels


# ============================================================
# EXCLUDE TOPIC
# ============================================================


def is_excluded_name(name):
    """
    Exclude auto-generated Topic channels.
    """

    lname = name.lower().strip()

    for dash_char in [
        "–",
        "—",
        "-",
    ]:
        lname = lname.replace(
            dash_char,
            "-",
        )

    if lname.endswith("- topic") or lname.endswith("-topic"):
        return True

    if lname.endswith("(topic)"):
        return True

    return False


# ============================================================
# CHANNEL DATA
# ============================================================


def get_channel_stats(
    youtube,
    channel_id,
):
    """
    Ambil:
      - subscriber
      - uploads playlist
      - country
    """

    response = (
        youtube.channels()
        .list(
            part=("snippet," "statistics," "contentDetails"),
            id=channel_id,
        )
        .execute()
    )

    items = response.get(
        "items",
        [],
    )

    if not items:
        return None

    item = items[0]

    stats = item["statistics"]

    if stats.get(
        "hiddenSubscriberCount",
        False,
    ):
        return None

    subs = int(
        stats.get(
            "subscriberCount",
            0,
        )
    )

    uploads_playlist = item["contentDetails"]["relatedPlaylists"]["uploads"]

    country = item["snippet"].get(
        "country",
        "N/A",
    )

    return {
        "subscribers": subs,
        "uploads_playlist": uploads_playlist,
        "country": country,
    }


# ============================================================
# LIST A
# ============================================================


def get_recent_videos(
    youtube,
    playlist_id,
    target_count,
):
    """
    List A:
    Video terbaru non-Shorts.
    """

    collected = []

    page_token = None

    pages_fetched = 0

    while len(collected) < target_count and pages_fetched < LIST_A_MAX_PAGES:

        response = (
            youtube.playlistItems()
            .list(
                part="contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
            .execute()
        )

        pages_fetched += 1

        items = response.get(
            "items",
            [],
        )

        if not items:
            break

        batch = []

        for item in items:

            vid = item["contentDetails"]["videoId"]

            published = item["contentDetails"].get("videoPublishedAt")

            batch.append(
                {
                    "video_id": vid,
                    "published_at": published,
                }
            )

        batch = attach_video_details(
            youtube,
            batch,
        )

        collected.extend(filter_shorts(batch))

        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return collected[:target_count]


# ============================================================
# LIST B
# ============================================================


def get_popular_videos(
    youtube,
    channel_id,
    target_count,
):
    """
    List B:
    Video terpopuler channel.

    Mengambil buffer 2x target.
    """

    collected = []

    page_token = None

    buffer_target = target_count * 2

    while len(collected) < buffer_target:

        kwargs = dict(
            part="snippet",
            channelId=channel_id,
            type="video",
            order="viewCount",
            maxResults=min(
                buffer_target - len(collected),
                50,
            ),
        )

        if page_token:
            kwargs["pageToken"] = page_token

        response = youtube.search().list(**kwargs).execute()

        items = response.get(
            "items",
            [],
        )

        if not items:
            break

        for item in items:

            vid = item["id"]["videoId"]

            published = item["snippet"]["publishedAt"]

            collected.append(
                {
                    "video_id": vid,
                    "published_at": published,
                }
            )

            if len(collected) >= buffer_target:
                break

        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return collected


# ============================================================
# VIDEO DETAILS
# ============================================================


def attach_video_details(
    youtube,
    videos,
):
    """
    Tambahkan:
      - views
      - duration
      - thumbnails
    """

    if not videos:
        return videos

    id_to_video = {v["video_id"]: v for v in videos}

    ids = list(id_to_video.keys())

    for i in range(
        0,
        len(ids),
        50,
    ):

        batch = ids[i : i + 50]

        response = (
            youtube.videos()
            .list(
                part=("snippet," "statistics," "contentDetails"),
                id=",".join(batch),
            )
            .execute()
        )

        for item in response.get(
            "items",
            [],
        ):

            views = int(
                item["statistics"].get(
                    "viewCount",
                    0,
                )
            )

            duration_sec = parse_duration_to_seconds(
                item["contentDetails"].get("duration")
            )

            id_to_video[item["id"]]["views"] = views

            id_to_video[item["id"]]["duration_seconds"] = duration_sec

            id_to_video[item["id"]]["is_short"] = duration_sec <= SHORT_MAX_SECONDS

            id_to_video[item["id"]]["thumbnails"] = item["snippet"].get(
                "thumbnails",
                {},
            )

    for v in videos:

        v.setdefault(
            "views",
            0,
        )

        v.setdefault(
            "duration_seconds",
            0,
        )

        v.setdefault(
            "is_short",
            False,
        )

        v.setdefault(
            "thumbnails",
            {},
        )

    return videos


# ============================================================
# FILTER SHORTS
# ============================================================


def filter_shorts(videos):

    if not EXCLUDE_SHORTS:
        return videos

    return [v for v in videos if not v["is_short"]]


# ============================================================
# RULE 1
# ============================================================


def check_rule1(list_a):

    if len(list_a) < MIN_VIDEOS_REQUIRED:
        return (
            False,
            "Data List A kurang",
        )

    rest = list_a[RULE1_EXEMPT_NEWEST:]

    if not rest:

        return (
            False,
            f"Tidak ada video tersisa setelah "
            f"{RULE1_EXEMPT_NEWEST} video terbaru di-skip",
        )

    low_views = []

    for idx, video in enumerate(
        rest,
        start=RULE1_EXEMPT_NEWEST + 1,
    ):

        views = video.get(
            "views",
            0,
        )

        if views < MIN_VIEWS_FLOOR:

            low_views.append(
                {
                    "video_number": idx,
                    "views": views,
                    "video_id": video["video_id"],
                    "published_at": video.get("published_at"),
                }
            )

    low_count = len(low_views)

    if low_count > RULE1_MAX_LOW_VIEWS:

        detail = "; ".join(
            f"Video #{v['video_number']} = " f"{v['views']:,} views" for v in low_views
        )

        return (
            False,
            f"{low_count} video di bawah minimum "
            f"{MIN_VIEWS_FLOOR:,} views "
            f"(maks {RULE1_MAX_LOW_VIEWS} video): "
            f"{detail}",
        )

    if low_count > 0:

        detail = "; ".join(
            f"Video #{v['video_number']} = " f"{v['views']:,} views" for v in low_views
        )

        return (
            True,
            f"{low_count} video di bawah minimum "
            f"{MIN_VIEWS_FLOOR:,} views tetapi masih "
            f"dalam toleransi: {detail}",
        )

    return (
        True,
        f"Semua video #"
        f"{RULE1_EXEMPT_NEWEST + 1}"
        f"-#{len(list_a)} memenuhi minimum "
        f"{MIN_VIEWS_FLOOR:,} views",
    )


# ============================================================
# RULE 2
# ============================================================


def check_rule2(list_b):

    if len(list_b) < MIN_VIDEOS_REQUIRED:
        return (
            False,
            "Data List B kurang",
        )

    now = datetime.datetime.utcnow()

    old_exempt_count = 0

    too_old_count = 0

    for v in list_b:

        if not v.get("published_at"):
            continue

        age = age_in_months(
            v["published_at"],
            now,
        )

        if age > RULE2_OLD_EXEMPT_MONTHS:

            too_old_count += 1

        elif age > RULE2_MONTHS:

            old_exempt_count += 1

    if too_old_count > 0:

        return (
            False,
            f"{too_old_count} video umur > "
            f"{RULE2_OLD_EXEMPT_MONTHS} bulan "
            f"(di luar toleransi)",
        )

    if old_exempt_count > RULE2_MAX_OLD_EXEMPT:

        return (
            False,
            f"{old_exempt_count} video umur "
            f"{RULE2_MONTHS}-"
            f"{RULE2_OLD_EXEMPT_MONTHS} bulan "
            f"(maks {RULE2_MAX_OLD_EXEMPT})",
        )

    return True, "OK"


# ============================================================
# RULE 3
# ============================================================


RULE3_MAX_DAYS_SINCE_LAST_UPLOAD = 30


def check_rule3(list_a):

    if not list_a or not list_a[0].get("published_at"):

        return (
            False,
            "Tidak ada data video terbaru",
        )

    last_upload = parse_iso(list_a[0]["published_at"])

    days_since = (datetime.datetime.utcnow() - last_upload).days

    if days_since > RULE3_MAX_DAYS_SINCE_LAST_UPLOAD:

        return (
            False,
            f"Upload terakhir {days_since} hari lalu "
            f"(maks {RULE3_MAX_DAYS_SINCE_LAST_UPLOAD} hari)",
        )

    return True, "OK"


# ============================================================
# UPLOAD INTERVAL
# ============================================================


def upload_interval_info(list_a):

    dates = [parse_iso(v["published_at"]) for v in list_a if v.get("published_at")]

    dates.sort(reverse=True)

    if len(dates) < 2:

        return (
            None,
            "N/A",
        )

    gaps = [(dates[i] - dates[i + 1]).days for i in range(len(dates) - 1)]

    avg_gap = sum(gaps) / len(gaps)

    label = "Upload jarang" if avg_gap >= MAX_UPLOAD_INTERVAL_DAYS else "Upload rutin"

    return (
        avg_gap,
        label,
    )


# ============================================================
# CSV
# ============================================================


CSV_FIELDNAMES = [
    "status",
    "channel",
    "channel_url",
    "subscribers",
    "country",
    "top_video_upload_hour",
    "avg_views",
    "min_views",
    "max_views",
    "max_min_ratio",
    "upload_label",
    "avg_gap_days",
    "meta_channel_path",
    "meta_populer_path",
    "reason",
]


def export_to_csv(
    passed,
    failed,
    genre_keyword,
    output_dir="output",
):

    safe_genre = sanitize_genre_name(genre_keyword)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    filename = f"{safe_genre}/" f"outlier_{timestamp}.csv"

    filepath = os.path.join(
        output_dir,
        filename,
    )

    os.makedirs(
        os.path.dirname(filepath),
        exist_ok=True,
    )

    with open(
        filepath,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=CSV_FIELDNAMES,
        )

        writer.writeheader()

        # ====================================================
        # PASS
        # ====================================================

        for r in passed:

            # SANITIZE NAMA CHANNEL
            safe_channel = sanitize_filename(r.get("channel"))

            writer.writerow(
                {
                    "status": "PASS",
                    "channel": safe_channel,
                    "channel_url": r.get("channel_url"),
                    "subscribers": r.get("subscribers"),
                    "country": r.get(
                        "country",
                        "N/A",
                    ),
                    "top_video_upload_hour": r.get(
                        "top_video_upload_hour",
                        "N/A",
                    ),
                    "avg_views": (
                        round(r["avg_views"]) if r.get("avg_views") is not None else ""
                    ),
                    "min_views": r.get("min_views"),
                    "max_views": r.get("max_views"),
                    "max_min_ratio": (
                        round(
                            r["max_min_ratio"],
                            3,
                        )
                        if r.get("max_min_ratio") is not None
                        else ""
                    ),
                    "upload_label": r.get("upload_label"),
                    "avg_gap_days": (
                        round(r["avg_gap_days"]) if r.get("avg_gap_days") else ""
                    ),
                    "meta_channel_path": r.get(
                        "meta_channel_path",
                        "",
                    ),
                    "meta_populer_path": r.get(
                        "meta_populer_path",
                        "",
                    ),
                    "reason": "",
                }
            )

        # ====================================================
        # FAIL
        # ====================================================

        for r in failed:

            safe_channel = sanitize_filename(r.get("channel"))

            writer.writerow(
                {
                    "status": "FAIL",
                    "channel": safe_channel,
                    "channel_url": r.get(
                        "channel_url",
                        f"https://www.youtube.com/channel/"
                        f"{r.get('channel_id', '')}",
                    ),
                    "subscribers": r.get(
                        "subscribers",
                        "",
                    ),
                    "country": "",
                    "top_video_upload_hour": "",
                    "avg_views": "",
                    "min_views": "",
                    "max_views": "",
                    "max_min_ratio": "",
                    "upload_label": r.get(
                        "upload_label",
                        "",
                    ),
                    "avg_gap_days": (
                        round(r["avg_gap_days"]) if r.get("avg_gap_days") else ""
                    ),
                    "meta_channel_path": "",
                    "meta_populer_path": "",
                    "reason": r.get(
                        "reason",
                        "?",
                    ),
                }
            )

    return filepath


# ============================================================
# MAIN
# ============================================================


def main():

    if not API_KEY:

        print("ERROR: YOUTUBE_API_KEY tidak ditemukan.")

        print("Buat file .env di folder ini, isi:")

        print("  YOUTUBE_API_KEY=api_key_kamu_di_sini")

        return

    youtube = build(
        "youtube",
        "v3",
        developerKey=API_KEY,
    )

    print(f"Mencari kandidat channel untuk genre: " f"'{GENRE_KEYWORD}'...\n")

    candidates = search_candidate_channels(
        youtube,
        GENRE_KEYWORD,
        NUM_CANDIDATE_VIDEOS,
        POPULAR_WITHIN_DAYS,
    )

    print(f"Ditemukan {len(candidates)} " f"channel kandidat mentah.\n")

    passed = []

    failed = []

    quota_exceeded = False

    # ========================================================
    # LOOP CHANNEL
    # ========================================================

    for cid, cname in candidates.items():

        # ----------------------------------------------------
        # EXCLUDE TOPIC
        # ----------------------------------------------------

        if is_excluded_name(cname):

            failed.append(
                {
                    "channel": cname,
                    "channel_id": cid,
                    "reason": ("Nama mengandung '- Topic' " "(auto-generated)"),
                }
            )

            continue

        try:

            # ------------------------------------------------
            # CHANNEL INFO
            # ------------------------------------------------

            info = get_channel_stats(
                youtube,
                cid,
            )

            if info is None:

                failed.append(
                    {
                        "channel": cname,
                        "channel_id": cid,
                        "reason": ("Subscriber hidden / " "data tidak valid"),
                    }
                )

                continue

            # ------------------------------------------------
            # SUBSCRIBER FILTER
            # ------------------------------------------------

            if info["subscribers"] >= MAX_SUBSCRIBERS:

                failed.append(
                    {
                        "channel": cname,
                        "channel_id": cid,
                        "reason": (f"Subscriber >= " f"{MAX_SUBSCRIBERS:,}"),
                    }
                )

                continue

            # ------------------------------------------------
            # LIST A
            # ------------------------------------------------

            list_a = get_recent_videos(
                youtube,
                info["uploads_playlist"],
                SAMPLE_SIZE,
            )

            # ------------------------------------------------
            # LIST B
            # ------------------------------------------------

            list_b = get_popular_videos(
                youtube,
                cid,
                SAMPLE_SIZE,
            )

            list_b = attach_video_details(
                youtube,
                list_b,
            )

            list_b = filter_shorts(list_b)[:SAMPLE_SIZE]

        except HttpError as e:

            if e.resp.status == 429 or "quota" in str(e).lower():

                print(f"\n⚠️ KUOTA API HABIS " f"saat memproses '{cname}'.")

                print("Menghentikan proses lebih awal.")

                print(
                    f"Progress: "
                    f"{len(passed) + len(failed)}/"
                    f"{len(candidates)} channel."
                )

                quota_exceeded = True

                break

            else:

                failed.append(
                    {
                        "channel": cname,
                        "channel_id": cid,
                        "reason": (f"API error: {e}"),
                    }
                )

                continue

        # ====================================================
        # RULE CHECK
        # ====================================================

        r1_pass, r1_detail = check_rule1(list_a)

        r2_pass, r2_detail = check_rule2(list_b)

        r3_pass, r3_detail = check_rule3(list_a)

        avg_gap, upload_label = upload_interval_info(list_a)

        # ====================================================
        # BASE RECORD
        # ====================================================

        base_record = {
            # Simpan nama asli di memory.
            # Akan disanitize saat CSV/output.
            "channel": cname,
            "channel_id": cid,
            "channel_url": (f"https://www.youtube.com/channel/" f"{cid}"),
            "subscribers": info["subscribers"],
            "upload_label": upload_label,
            "avg_gap_days": avg_gap,
        }

        # ====================================================
        # PASS
        # ====================================================

        if r1_pass and r2_pass and r3_pass:

            views_a = [v["views"] for v in list_a]

            max_v = max(views_a)

            min_v = min(views_a)

            ratio = max_v / min_v if min_v > 0 else float("inf")

            base_record.update(
                {
                    "max_min_ratio": ratio,
                    "avg_views": statistics.mean(views_a),
                    "min_views": min_v,
                    "max_views": max_v,
                    "country": info.get(
                        "country",
                        "N/A",
                    ),
                }
            )

            # =================================================
            # 10 THUMBNAIL TERPOPULER
            # =================================================
            #
            # list_b sudah diurutkan berdasarkan
            # viewCount dari YouTube.
            #
            # Jadi 10 pertama adalah 10 video
            # terpopuler.
            # =================================================

            top_videos_for_thumbnails = list_b[:THUMBNAIL_COUNT]

            # -------------------------------------------------
            # Video paling populer
            # -------------------------------------------------

            most_popular_video = (
                max(
                    list_b,
                    key=lambda v: v.get(
                        "views",
                        0,
                    ),
                )
                if list_b
                else None
            )

            base_record["top_video_upload_hour"] = upload_hour_wib(
                most_popular_video.get("published_at") if most_popular_video else None
            )

            # -------------------------------------------------
            # Buat contact sheet
            # -------------------------------------------------

            thumb_path = create_thumbnail_contact_sheet(
                top_videos_for_thumbnails,
                CSV_OUTPUT_DIR,
                GENRE_KEYWORD,
                cname,
            )

            if thumb_path:

                base_record["thumbnail_path"] = thumb_path

            # -------------------------------------------------
            # META CHANNEL: metadata LENGKAP channel (BARU)
            # -------------------------------------------------

            safe_genre = sanitize_genre_name(GENRE_KEYWORD)

            safe_channel = sanitize_filename(cname)

            channel_folder = os.path.join(
                CSV_OUTPUT_DIR,
                safe_genre,
                safe_channel,
            )

            full_channel_meta = get_full_channel_metadata(
                youtube,
                cid,
            )

            if full_channel_meta:

                meta_channel_path = os.path.join(
                    channel_folder,
                    META_CHANNEL_FILENAME,
                )

                saved_path = save_json(
                    full_channel_meta,
                    meta_channel_path,
                )

                if saved_path:

                    base_record["meta_channel_path"] = saved_path

                    print(f"  🗂️ meta_channel disimpan: {saved_path}")

            # -------------------------------------------------
            # META POPULER: metadata LENGKAP 5 video
            # terpopuler (BARU)
            # -------------------------------------------------
            #
            # list_b sudah diurutkan berdasarkan viewCount,
            # jadi META_POPULER_COUNT pertama adalah video
            # paling populer.
            # -------------------------------------------------

            top5_ids = [
                v["video_id"] for v in list_b[:META_POPULER_COUNT] if v.get("video_id")
            ]

            full_populer_meta = get_full_video_metadata(
                youtube,
                top5_ids,
            )

            if full_populer_meta:

                meta_populer_path = os.path.join(
                    channel_folder,
                    META_POPULER_FILENAME,
                )

                saved_path = save_json(
                    full_populer_meta,
                    meta_populer_path,
                )

                if saved_path:

                    base_record["meta_populer_path"] = saved_path

                    print(f"  🗂️ meta_populer disimpan: {saved_path}")

            passed.append(base_record)

        # ====================================================
        # FAIL
        # ====================================================

        else:

            reasons = []

            if not r1_pass:

                reasons.append(f"Rule1: {r1_detail}")

            if not r2_pass:

                reasons.append(f"Rule2: {r2_detail}")

            if not r3_pass:

                reasons.append(f"Rule3: {r3_detail}")

            base_record["reason"] = " | ".join(reasons)

            failed.append(base_record)

    # ========================================================
    # SORT PASS
    # ========================================================

    passed.sort(key=lambda r: r["max_min_ratio"])

    # ========================================================
    # EXPORT CSV
    # ========================================================

    csv_path = export_to_csv(
        passed,
        failed,
        GENRE_KEYWORD,
        CSV_OUTPUT_DIR,
    )

    # ========================================================
    # QUOTA WARNING
    # ========================================================

    if quota_exceeded:

        print("=" * 110)

        print("⚠️ HASIL TIDAK LENGKAP " "-- proses berhenti karena kuota API habis.")

        print("=" * 110)

    # ========================================================
    # PASS OUTPUT
    # ========================================================

    print("=" * 110)

    print(f"CHANNEL LOLOS ({len(passed)})")

    print("=" * 110)

    print(
        f"{'#':>3} "
        f"{'Channel':28} "
        f"{'Subs':>8} "
        f"{'AvgViews':>10} "
        f"{'Max/Min':>8} "
        f"{'Upload':>14}"
    )

    print("-" * 110)

    for i, r in enumerate(
        passed,
        start=1,
    ):

        gap_str = f"{r['avg_gap_days']:.0f}d" if r["avg_gap_days"] else "N/A"

        safe_channel = sanitize_filename(r["channel"])

        print(
            f"{i:>3} "
            f"{safe_channel[:26]:28} "
            f"{r['subscribers']:>8,} "
            f"{r['avg_views']:>10,.0f} "
            f"{r['max_min_ratio']:>8.2f} "
            f"{r['upload_label']} "
            f"({gap_str})"
        )

        print(f"    -> {r['channel_url']}")

        print(
            f"    -> Country: "
            f"{r.get('country', 'N/A')} | "
            f"Jam upload video terpopuler: "
            f"{r.get('top_video_upload_hour', 'N/A')}"
        )

        if r.get("thumbnail_path"):

            print(f"    -> 10 Thumbnail: " f"{r['thumbnail_path']}")

        if r.get("meta_channel_path"):

            print(f"    -> meta_channel: " f"{r['meta_channel_path']}")

        if r.get("meta_populer_path"):

            print(f"    -> meta_populer: " f"{r['meta_populer_path']}")

    # ========================================================
    # FAIL OUTPUT
    # ========================================================

    print(f"\n{'=' * 110}")

    print(f"CHANNEL GUGUR ({len(failed)}) " f"-- untuk audit manual")

    print("=" * 110)

    for r in failed:

        safe_channel = sanitize_filename(r["channel"])

        reason = r.get(
            "reason",
            "?",
        )

        url = r.get(
            "channel_url",
            f"https://www.youtube.com/channel/" f"{r['channel_id']}",
        )

        print(f"- {safe_channel[:40]:40} | " f"{reason}")

        print(f"  -> {url}")

    # ========================================================
    # CATATAN
    # ========================================================

    print("\nCatatan:")

    print(
        f"- Rule 1: "
        f"{RULE1_EXEMPT_NEWEST} video terbaru di-skip, "
        f"sisanya dicek per-video, wajib >= "
        f"{MIN_VIEWS_FLOOR:,} views "
        f"(toleransi maks "
        f"{RULE1_MAX_LOW_VIEWS} video)."
    )

    print(
        f"- Rule 2: video populer > "
        f"{RULE2_OLD_EXEMPT_MONTHS} bulan langsung gugur; "
        f"umur {RULE2_MONTHS}-"
        f"{RULE2_OLD_EXEMPT_MONTHS} bulan maks "
        f"{RULE2_MAX_OLD_EXEMPT} video."
    )

    print(
        f"- Rule 3: channel wajib upload dalam "
        f"{RULE3_MAX_DAYS_SINCE_LAST_UPLOAD} hari terakhir."
    )

    print("- Label 'Upload jarang/rutin' hanya informasi.")

    print("- Max/Min ratio: makin dekat 1.00 = makin stabil.")

    print(
        f"- Setiap channel PASS menyimpan "
        f"{THUMBNAIL_COUNT} thumbnail terpopuler "
        f"dalam satu gambar grid {THUMBNAIL_ROWS}x"
        f"{THUMBNAIL_COLUMNS}."
    )

    print(
        f"- Contact sheet: "
        f"{CSV_OUTPUT_DIR}/<genre>/<nama_channel>/"
        f"thumbnails_10.jpg"
    )

    print(f"- meta_channel.json: metadata LENGKAP channel " f"({CHANNEL_META_PARTS}).")

    print(
        f"- meta_populer.json: metadata LENGKAP "
        f"{META_POPULER_COUNT} video terpopuler "
        f"({VIDEO_META_PARTS})."
    )

    print(
        f"- Lokasi meta: "
        f"{CSV_OUTPUT_DIR}/<genre>/<nama_channel>/"
        f"meta_channel.json & meta_populer.json"
    )

    print("- Nama channel di CSV dan folder sudah di-sanitize.")

    print("- channel_url tetap menggunakan Channel ID asli.")

    print(f"\nHasil juga disimpan ke CSV: " f"{csv_path}")


if __name__ == "__main__":
    main()
