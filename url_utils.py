"""
URL normalization utilities for YTdvy.

Handles the many ways a YouTube link can show up, especially from mobile
sharing, and figures out whether it's a single video, a playlist, or a
video-within-a-playlist (in which case we treat it as a single video,
since that's almost always the user's intent when they share that link).
"""
import re
from urllib.parse import urlparse, parse_qs, urlunparse


class LinkType:
    VIDEO = "video"
    PLAYLIST = "playlist"
    INVALID = "invalid"


# Domains we accept as YouTube
VALID_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
}

VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")
PLAYLIST_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _strip_tracking_params(url: str) -> str:
    """Remove the share-tracking junk mobile apps tack on (si=, feature=, etc)."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    # Tracking / non-functional params we don't need
    junk_params = {"si", "feature", "pp", "ab_channel", "utm_source",
                    "utm_medium", "utm_campaign", "fbclid", "gclid"}
    cleaned_qs = {k: v for k, v in qs.items() if k not in junk_params}
    return parsed._replace(query="&".join(
        f"{k}={v[0]}" for k, v in cleaned_qs.items()
    )).geturl()


def normalize_youtube_url(raw_url: str):
    """
    Takes any raw YouTube URL (desktop, mobile, shortened, with tracking
    params, with timestamps, etc.) and returns a dict:
        {
            "type": LinkType.VIDEO | LinkType.PLAYLIST | LinkType.INVALID,
            "video_id": str or None,
            "playlist_id": str or None,
            "clean_url": str or None,   # canonical URL to feed to yt-dlp
            "error": str or None,
        }

    Handles:
      - https://www.youtube.com/watch?v=ID
      - https://youtu.be/ID  (mobile share links)
      - https://youtu.be/ID?si=xxxxx (mobile share links with tracking)
      - https://m.youtube.com/watch?v=ID  (mobile web)
      - https://music.youtube.com/watch?v=ID
      - https://www.youtube.com/shorts/ID
      - https://www.youtube.com/embed/ID
      - https://www.youtube.com/live/ID
      - https://www.youtube.com/playlist?list=ID
      - https://www.youtube.com/watch?v=ID&list=ID  (video inside playlist
        -> treated as VIDEO, since opening a single video is the intent)
      - URLs with extra params like &t=42s, &index=3, ?si=, etc.
      - Missing scheme (e.g. "youtu.be/abc123" or "www.youtube.com/watch?v=abc")
      - Raw 11-character video IDs typed directly
    """
    if not raw_url or not raw_url.strip():
        return {"type": LinkType.INVALID, "video_id": None,
                "playlist_id": None, "clean_url": None,
                "error": "Empty input."}

    raw_url = raw_url.strip()

    # Allow pasting a bare video ID
    if VIDEO_ID_RE.match(raw_url):
        return {
            "type": LinkType.VIDEO,
            "video_id": raw_url,
            "playlist_id": None,
            "clean_url": f"https://www.youtube.com/watch?v={raw_url}",
            "error": None,
        }

    # Add a scheme if missing (common when copy-pasted from mobile without https://)
    if not re.match(r"^https?://", raw_url, re.IGNORECASE):
        raw_url = "https://" + raw_url

    try:
        parsed = urlparse(raw_url)
    except Exception:
        return {"type": LinkType.INVALID, "video_id": None,
                "playlist_id": None, "clean_url": None,
                "error": "Could not parse URL."}

    host = parsed.netloc.lower()
    # Strip a leading "www." duplicate edge case and port if any
    host = host.split(":")[0]

    if host not in VALID_HOSTS:
        return {"type": LinkType.INVALID, "video_id": None,
                "playlist_id": None, "clean_url": None,
                "error": f"'{host}' is not a recognized YouTube domain."}

    path = parsed.path.rstrip("/")
    qs = parse_qs(parsed.query)

    video_id = None
    playlist_id = qs.get("list", [None])[0]

    # youtu.be/<id>  (the classic mobile/short-link share format)
    if host in ("youtu.be", "www.youtu.be"):
        segments = [s for s in path.split("/") if s]
        if segments:
            video_id = segments[0]

    # youtube.com/watch?v=<id>
    elif path in ("/watch",):
        video_id = qs.get("v", [None])[0]

    # youtube.com/shorts/<id>
    elif path.startswith("/shorts/"):
        video_id = path.split("/shorts/", 1)[1].split("/")[0]

    # youtube.com/embed/<id>
    elif path.startswith("/embed/"):
        video_id = path.split("/embed/", 1)[1].split("/")[0]

    # youtube.com/live/<id>
    elif path.startswith("/live/"):
        video_id = path.split("/live/", 1)[1].split("/")[0]

    # youtube.com/playlist?list=<id>  (playlist page, no specific video)
    elif path == "/playlist":
        playlist_id = qs.get("list", [None])[0]

    # Clean stray query fragments from video id (e.g. "ID&t=10s" edge cases
    # already handled by parse_qs, but guard against trailing punctuation)
    if video_id:
        video_id = video_id.split("?")[0].split("&")[0]

    # Decide final classification.
    # If both a video id AND a playlist id are present (e.g. user opened a
    # video from inside a playlist), we treat it as a single VIDEO download,
    # since that matches "if it's a video, just download the video".
    if video_id:
        if not VIDEO_ID_RE.match(video_id):
            return {"type": LinkType.INVALID, "video_id": None,
                    "playlist_id": None, "clean_url": None,
                    "error": f"Video ID '{video_id}' doesn't look valid."}
        return {
            "type": LinkType.VIDEO,
            "video_id": video_id,
            "playlist_id": playlist_id,
            "clean_url": f"https://www.youtube.com/watch?v={video_id}",
            "error": None,
        }

    if playlist_id:
        if not PLAYLIST_ID_RE.match(playlist_id):
            return {"type": LinkType.INVALID, "video_id": None,
                    "playlist_id": None, "clean_url": None,
                    "error": f"Playlist ID '{playlist_id}' doesn't look valid."}
        return {
            "type": LinkType.PLAYLIST,
            "video_id": None,
            "playlist_id": playlist_id,
            "clean_url": f"https://www.youtube.com/playlist?list={playlist_id}",
            "error": None,
        }

    return {"type": LinkType.INVALID, "video_id": None,
            "playlist_id": None, "clean_url": None,
            "error": "Could not find a video or playlist ID in that URL."}


if __name__ == "__main__":
    # Quick manual smoke test of all the variants we claim to support
    test_urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=AbCdEf12345",
        "youtu.be/dQw4w9WgXcQ",  # no scheme
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVM",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxxxxxxxxxxxxxxxx&index=3",
        "https://www.youtube.com/playlist?list=PLxxxxxxxxxxxxxxxx",
        "dQw4w9WgXcQ",  # raw ID
        "https://www.google.com/watch?v=abc",  # invalid host
        "not a url at all",
        "",
    ]
    for u in test_urls:
        result = normalize_youtube_url(u)
        print(f"{u!r:75} -> {result}")
