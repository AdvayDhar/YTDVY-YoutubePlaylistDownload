"""
Core download + merge logic for YTdvy.

- Single video link  -> download as-is via yt-dlp.
- Playlist link      -> download every video in the playlist, then
                        concatenate them into a single output file with
                        ffmpeg, in playlist order.

Designed to run jobs in a background thread and report progress via a
shared dict so the Flask app can poll status.
"""
import os
import random
import re
import shutil
import subprocess
import time
import uuid

import yt_dlp

from url_utils import LinkType, normalize_youtube_url

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
TEMP_DIR = os.path.join(BASE_DIR, "temp")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# --- Bot-check / auth config -------------------------------------------
#
# Diagnosed via tester.py: this setup does NOT currently need cookies at
# all. Bare/no-auth requests work fine, and the "android" player client
# specifically works, while "web" (which was previously forced on
# alongside "android") returns restricted format lists and causes
# failures. So: no cookies, pin to "android" only.
#
# If bot-checks start happening later (YouTube changes things often),
# set COOKIES_FILE_PATH below to a cookies.txt file. Generate one with:
#   yt-dlp --cookies-from-browser chrome --cookies cookies.txt https://www.youtube.com
# Re-run tester.py after any change to confirm what's actually working
# before assuming cookies are the fix.
COOKIES_FILE_PATH = os.path.join(BASE_DIR, "cookies.txt")            # e.g. "cookies.txt"
COOKIES_FROM_BROWSER = None         # unreliable if browser is open; avoid

# Player client(s) to identify as. "android" confirmed working via
# tester.py; "web" confirmed broken (restricted formats) on this setup.
PLAYER_CLIENTS = ["android"]

# Small randomized delay between video downloads in a playlist. Hammering
# YouTube with rapid back-to-back requests is itself a bot-detection signal,
# so spacing requests out a bit makes the traffic look more human.
MIN_SLEEP_BETWEEN_DOWNLOADS = 2     # seconds
MAX_SLEEP_BETWEEN_DOWNLOADS = 5     # seconds


def _auth_opts() -> dict:
    """Returns the cookie-auth portion of yt-dlp options, if configured."""
    opts = {}
    if COOKIES_FILE_PATH:
        opts["cookiefile"] = COOKIES_FILE_PATH
    elif COOKIES_FROM_BROWSER:
        # yt-dlp expects a tuple: (browser, profile, keyring, container)
        opts["cookiesfrombrowser"] = (COOKIES_FROM_BROWSER, None, None, None)
    return opts

# Quality format strings yt-dlp understands
QUALITY_FORMATS = {
    "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
    "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
    "480p": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]",
    "audio": "bestaudio/best",
}


def _safe_filename(name: str) -> str:
    """Strip characters that are awkward in filenames across OSes."""
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    return name.strip()[:150] or "output"


def _friendly_error(e: Exception) -> str:
    """Turns common yt-dlp errors into messages that actually tell you what to do."""
    msg = str(e)
    lowered = msg.lower()
    if "sign in to confirm" in lowered and "bot" in lowered:
        if COOKIES_FILE_PATH and not os.path.exists(COOKIES_FILE_PATH):
            return (
                f"YouTube's bot check blocked this download, and no cookies file was "
                f"found at '{COOKIES_FILE_PATH}'. Run this once in your terminal from "
                f"the project folder: yt-dlp --cookies-from-browser chrome --cookies "
                f"cookies.txt https://www.youtube.com  (swap 'chrome' for your browser), "
                f"then restart the app."
            )
        return (
            "YouTube's bot check blocked this download. Run tester.py to check which "
            "player clients currently work, and update PLAYER_CLIENTS in downloader.py "
            "accordingly. If cookies are configured, they may have expired — re-run: "
            "yt-dlp --cookies-from-browser chrome --cookies cookies.txt "
            "https://www.youtube.com to refresh."
        )
    if "requested format is not available" in lowered:
        return (
            "yt-dlp couldn't find a matching format for this video with the current "
            "player client. Run tester.py to see which player clients currently return "
            "full format lists, and update PLAYER_CLIENTS in downloader.py to match."
        )
    if "http error 429" in lowered or "too many requests" in lowered:
        return (
            "YouTube is rate-limiting this IP (too many requests). Wait a while "
            "before trying again, or lower the request rate."
        )
    if "private video" in lowered or "video unavailable" in lowered:
        return "This video is private, deleted, or otherwise unavailable."
    return msg


class Job:
    """In-memory representation of a single download job and its progress."""

    def __init__(self, job_id, link_type, quality):
        self.id = job_id
        self.link_type = link_type
        self.quality = quality
        self.status = "queued"       # queued | fetching_info | downloading | merging | done | error
        self.message = "Queued..."
        self.progress = 0            # 0-100
        self.total_items = 0
        self.completed_items = 0
        self.output_path = None      # final file, relative to DOWNLOADS_DIR
        self.error = None
        self.work_dir = os.path.join(TEMP_DIR, job_id)

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "progress": self.progress,
            "total_items": self.total_items,
            "completed_items": self.completed_items,
            "output_path": self.output_path,
            "error": self.error,
        }


# Simple in-memory job registry. Fine for a single-user local app.
JOBS = {}


def create_job(link_type, quality):
    job_id = uuid.uuid4().hex[:12]
    job = Job(job_id, link_type, quality)
    JOBS[job_id] = job
    return job


def _make_progress_hook(job: Job, item_label_prefix=""):
    def hook(d):
        if d["status"] == "downloading":
            job.status = "downloading"
            pct_str = d.get("_percent_str", "0%").strip().replace("%", "")
            try:
                pct = float(pct_str)
            except ValueError:
                pct = 0
            job.message = f"{item_label_prefix}Downloading... {pct:.0f}%"
        elif d["status"] == "finished":
            job.message = f"{item_label_prefix}Processing downloaded file..."
    return hook


def _download_single(url: str, out_dir: str, quality: str, job: Job, index=None, total=None) -> str:
    """Downloads one video, returns the path to the resulting file.

    Retries once on failure after a short delay — some bot-check errors are
    transient and clear up on a second attempt a few seconds later.
    """
    label = f"[{index}/{total}] " if index and total else ""
    ydl_opts = {
        "format": QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"]),
        "outtmpl": os.path.join(out_dir, "%(playlist_index|)s%(title).150s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "merge_output_format": "mp4" if quality != "audio" else None,
        "progress_hooks": [_make_progress_hook(job, label)],
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": False,
        "extractor_args": {"youtube": {"player_client": PLAYER_CLIENTS}},
        **_auth_opts(),
    }
    if quality == "audio":
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    last_error = None
    for attempt in range(2):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                if quality == "audio":
                    base, _ = os.path.splitext(filename)
                    filename = base + ".mp3"
                return filename
        except Exception as e:
            last_error = e
            if attempt == 0:
                job.message = f"{label}Hit an error, retrying in a few seconds..."
                time.sleep(6)
    raise last_error


def _ffmpeg_concat(file_list, output_path, job: Job):
    """Concatenate videos in order using ffmpeg's concat demuxer.

    Falls back to re-encoding (slower, but handles mismatched codecs/
    resolutions between source videos) if the fast stream-copy concat fails.
    """
    job.status = "merging"
    job.message = "Merging videos into a single file..."

    concat_list_path = os.path.join(os.path.dirname(output_path), "concat_list.txt")
    with open(concat_list_path, "w") as f:
        for path in file_list:
            escaped = path.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    # Attempt 1: fast stream copy (works only if all inputs share codec/params)
    fast_cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_list_path, "-c", "copy", output_path,
    ]
    result = subprocess.run(fast_cmd, capture_output=True, text=True)

    if result.returncode != 0 or not os.path.exists(output_path):
        # Attempt 2: re-encode to normalize mismatched streams
        job.message = "Source videos differ in format — re-encoding to merge (slower)..."
        reencode_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-c:v", "libx264", "-c:a", "aac",
            "-vsync", "2",
            output_path,
        ]
        result2 = subprocess.run(reencode_cmd, capture_output=True, text=True)
        if result2.returncode != 0:
            raise RuntimeError(f"ffmpeg merge failed:\n{result2.stderr[-2000:]}")

    return output_path


def run_job(job: Job, raw_url: str):
    """Main worker function — run in a background thread."""
    try:
        os.makedirs(job.work_dir, exist_ok=True)
        norm = normalize_youtube_url(raw_url)

        if norm["type"] == LinkType.INVALID:
            job.status = "error"
            job.error = norm["error"] or "Invalid YouTube link."
            return

        if norm["type"] == LinkType.VIDEO:
            job.status = "downloading"
            job.total_items = 1
            job.message = "Downloading video..."
            downloaded_path = _download_single(
                norm["clean_url"], job.work_dir, job.quality, job
            )
            job.completed_items = 1
            job.progress = 90

            final_name = _safe_filename(os.path.basename(downloaded_path))
            final_path = os.path.join(DOWNLOADS_DIR, final_name)
            shutil.move(downloaded_path, final_path)
            job.output_path = final_name
            job.progress = 100
            job.status = "done"
            job.message = "Done."
            return

        if norm["type"] == LinkType.PLAYLIST:
            job.status = "fetching_info"
            job.message = "Reading playlist contents..."

            with yt_dlp.YoutubeDL({
                "quiet": True,
                "extract_flat": True,
                "extractor_args": {"youtube": {"player_client": PLAYER_CLIENTS}},
                **_auth_opts(),
            }) as ydl:
                info = ydl.extract_info(norm["clean_url"], download=False)

            entries = [e for e in info.get("entries", []) if e]
            job.total_items = len(entries)
            if job.total_items == 0:
                job.status = "error"
                job.error = "Playlist appears to be empty or private/unavailable."
                return

            playlist_title = _safe_filename(info.get("title") or "playlist")

            downloaded_files = []
            for idx, entry in enumerate(entries, start=1):
                video_url = entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}"
                job.message = f"Downloading video {idx}/{job.total_items}..."
                try:
                    path = _download_single(
                        video_url, job.work_dir, job.quality, job,
                        index=idx, total=job.total_items
                    )
                    downloaded_files.append(path)
                except Exception as e:
                    # Skip videos that fail (deleted/private/region-locked) but keep going
                    job.message = f"Skipped video {idx}/{job.total_items} (unavailable): {_friendly_error(e)}"
                    continue
                job.completed_items = idx
                job.progress = int((idx / job.total_items) * 80)  # reserve last 20% for merge

                # Space out requests a bit; rapid back-to-back hits look bot-like
                if idx < job.total_items:
                    time.sleep(random.uniform(MIN_SLEEP_BETWEEN_DOWNLOADS, MAX_SLEEP_BETWEEN_DOWNLOADS))

            if not downloaded_files:
                job.status = "error"
                job.error = "None of the videos in the playlist could be downloaded."
                return

            ext = ".mp3" if job.quality == "audio" else ".mp4"
            output_filename = f"{playlist_title}{ext}"
            merged_path = os.path.join(job.work_dir, output_filename)

            if len(downloaded_files) == 1:
                # Nothing to merge
                merged_path = downloaded_files[0]
            else:
                _ffmpeg_concat(downloaded_files, merged_path, job)

            final_name = _safe_filename(os.path.basename(merged_path))
            final_path = os.path.join(DOWNLOADS_DIR, final_name)
            shutil.move(merged_path, final_path)
            job.output_path = final_name
            job.progress = 100
            job.status = "done"
            job.message = f"Done. Merged {len(downloaded_files)}/{job.total_items} videos."
            return

    except Exception as e:
        job.status = "error"
        job.error = _friendly_error(e)
    finally:
        # Clean up per-video temp files (keep only the final moved file)
        try:
            if os.path.isdir(job.work_dir):
                shutil.rmtree(job.work_dir, ignore_errors=True)
        except Exception:
            pass