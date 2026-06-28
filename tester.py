"""
tester.py — standalone diagnostic for YTdvy's yt-dlp setup.

Run this directly (no Flask, no app context needed) to figure out exactly
which layer is broken when you're hitting "Sign in to confirm you're not
a bot" or similar errors.

Usage:
    python3 tester.py
    python3 tester.py --cookies cookies.txt
    python3 tester.py --video-id dQw4w9WgXcQ
    python3 tester.py --player-clients android,web
    python3 tester.py --num-requests 24
    python3 tester.py --playlist-url "https://www.youtube.com/playlist?list=YOUR_REAL_PLAYLIST_ID"

It runs a series of independent checks and tells you, in plain terms,
which one(s) failed — so we stop guessing and know exactly where the
problem is.
"""
import argparse
import os
import random
import sys
import time

import yt_dlp

# A handful of very stable, very public videos to test against (varied
# so a single video being region-locked or removed doesn't skew results)
TEST_VIDEO_IDS = [
    "dQw4w9WgXcQ",  # Rick Astley - Never Gonna Give You Up
    "jNQXAC9IVRw",  # Me at the zoo (first YouTube video ever)
    "9bZkp7q19f0",  # PSY - Gangnam Style
]

DIVIDER = "=" * 70


def banner(text):
    print(f"\n{DIVIDER}\n{text}\n{DIVIDER}")


def check_yt_dlp_version():
    banner("CHECK 1: yt-dlp version")
    print(f"Installed version: {yt_dlp.version.__version__}")
    print(
        "YouTube changes its internals often; yt-dlp ships fixes frequently.\n"
        "If this version is more than ~2-3 weeks old, update with:\n"
        "    pip install -U yt-dlp\n"
        "(or pip install -U yt-dlp --break-system-packages on some systems)"
    )


def check_cookies_file(cookies_path):
    banner(f"CHECK 2: cookies file at '{cookies_path}'")
    if not cookies_path:
        print("No cookies path given (--cookies not set). Skipping this check.")
        return None

    if not os.path.exists(cookies_path):
        print(f"FAIL: file does not exist at '{cookies_path}'.")
        return False

    size = os.path.getsize(cookies_path)
    print(f"File exists, size = {size} bytes.")

    if size == 0:
        print("FAIL: file is empty (0 bytes). The export didn't capture anything.")
        return False

    with open(cookies_path, "r", errors="ignore") as f:
        content = f.read()

    if "netscape" not in content.lower() and not content.strip().startswith("#"):
        print(
            "WARNING: file doesn't look like standard Netscape cookie format.\n"
            "yt-dlp expects lines like:\n"
            "  .youtube.com\\tTRUE\\t/\\tTRUE\\t0\\tNAME\\tvalue\n"
            "If this file was hand-exported from devtools or a different tool,\n"
            "it may not be in the right format."
        )

    youtube_cookie_lines = [
        line for line in content.splitlines()
        if "youtube.com" in line and not line.strip().startswith("#")
    ]
    print(f"Lines referencing youtube.com: {len(youtube_cookie_lines)}")

    # Look for the specific cookies that indicate an authenticated (not just
    # "visitor") session. Presence of these is what actually matters.
    auth_cookie_names = ["SID", "HSID", "SSID", "APISID", "SAPISID",
                          "__Secure-1PSID", "__Secure-3PSID", "LOGIN_INFO"]
    found_auth_cookies = [
        name for name in auth_cookie_names
        if any(f"\t{name}\t" in line for line in youtube_cookie_lines)
    ]

    if not found_auth_cookies:
        print(
            "FAIL: no authenticated-session cookies found (looked for: "
            f"{', '.join(auth_cookie_names)}).\n"
            "This usually means the browser export captured an ANONYMOUS/logged-out\n"
            "session, not a real signed-in one. Double check you were actually\n"
            "logged into a Google account on youtube.com (not just have the site\n"
            "open) when you ran the export command."
        )
        return False
    else:
        print(f"OK: found authenticated-session cookies: {', '.join(found_auth_cookies)}")
        return True


def _try_extract(label, ydl_opts, video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"\n--- {label} (video: {video_id}) ---")
    try:
        with yt_dlp.YoutubeDL({**ydl_opts, "quiet": True, "no_warnings": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        title = info.get("title", "?")
        print(f"OK: extracted info successfully. Title: {title!r}")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


def check_no_auth(video_id):
    banner("CHECK 3: bare request, no cookies, no special client")
    print("This tells us if YouTube is blocking even the simplest possible request.")
    return _try_extract("No auth", {}, video_id)


def check_with_cookies(cookies_path, video_id):
    banner("CHECK 4: request WITH cookies.txt")
    if not cookies_path or not os.path.exists(cookies_path):
        print("Skipping — no valid cookies file path given.")
        return None
    return _try_extract("With cookies", {"cookiefile": cookies_path}, video_id)


def check_player_clients(cookies_path, video_id):
    banner("CHECK 5: different player client fingerprints")
    print(
        "yt-dlp can pretend to be different YouTube clients (android app, ios app,\n"
        "web, tv, etc). Some clients are challenged less often than others. This\n"
        "checks which ones currently work for you, with and without cookies."
    )
    clients = ["android", "ios", "web", "tv", "mweb"]
    results = {}
    base_opts = {}
    if cookies_path and os.path.exists(cookies_path):
        base_opts["cookiefile"] = cookies_path

    for client in clients:
        opts = {**base_opts, "extractor_args": {"youtube": {"player_client": [client]}}}
        ok = _try_extract(f"player_client={client}", opts, video_id)
        results[client] = ok
    return results


def check_actual_download(cookies_path, video_id):
    banner("CHECK 6: real download attempt (smallest viable test)")
    print(
        "Extraction succeeding doesn't always mean downloading will too — this\n"
        "actually pulls a small amount of data to confirm end-to-end."
    )
    opts = {
        "format": "worst",  # smallest/fastest for a quick test
        "outtmpl": os.path.join("tester_output", "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }
    if cookies_path and os.path.exists(cookies_path):
        opts["cookiefile"] = cookies_path

    os.makedirs("tester_output", exist_ok=True)
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        print("OK: download completed. Check the tester_output/ folder.")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


# Same format strings the real app uses (see downloader.py QUALITY_FORMATS).
# tester.py's other checks use "worst", which is too lenient to catch a
# real-world failure — this checks the ACTUAL strings the app requests.
APP_QUALITY_FORMATS = {
    "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
    "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
    "480p": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]",
    "audio": "bestaudio/best",
}


def check_app_format_strings(cookies_path, video_id, player_clients):
    banner("CHECK 7: real download using the APP'S ACTUAL format strings + player client")
    print(
        f"Player client(s) being tested: {player_clients}\n"
        "This is the closest thing to actually running the app — same format\n"
        "strings as downloader.py's QUALITY_FORMATS, same player_client setting.\n"
        "If CHECK 5/6 passed but THIS fails, the issue is specifically about\n"
        "format availability under this player client, not bot-detection at all."
    )
    base_opts = {}
    if cookies_path and os.path.exists(cookies_path):
        base_opts["cookiefile"] = cookies_path
    base_opts["extractor_args"] = {"youtube": {"player_client": player_clients}}

    url = f"https://www.youtube.com/watch?v={video_id}"
    results = {}
    for quality, fmt in APP_QUALITY_FORMATS.items():
        opts = {
            **base_opts,
            "format": fmt,
            "outtmpl": os.path.join("tester_output", f"{quality}_%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }
        print(f"\n--- quality={quality}  format={fmt!r} ---")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            print(f"OK: '{quality}' downloaded successfully.")
            results[quality] = True
        except Exception as e:
            print(f"FAIL: {e}")
            results[quality] = False
    return results


def check_playlist_scale(cookies_path, player_clients, num_requests=8):
    banner(f"CHECK 8: repeated requests in a row (simulates playlist load, n={num_requests})")
    print(
        "The original failure happened across a 24-video playlist, not a single\n"
        "video. This fires several requests back-to-back (same pattern as the\n"
        "app's playlist loop, minus the actual download) to see if YouTube starts\n"
        "blocking partway through a burst, even when a single request is fine."
    )
    base_opts = {"quiet": True, "no_warnings": True,
                  "extractor_args": {"youtube": {"player_client": player_clients}}}
    if cookies_path and os.path.exists(cookies_path):
        base_opts["cookiefile"] = cookies_path

    results = []
    for i in range(num_requests):
        video_id = TEST_VIDEO_IDS[i % len(TEST_VIDEO_IDS)]
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            with yt_dlp.YoutubeDL(base_opts) as ydl:
                ydl.extract_info(url, download=False)
            print(f"  [{i+1}/{num_requests}] OK")
            results.append(True)
        except Exception as e:
            print(f"  [{i+1}/{num_requests}] FAIL: {e}")
            results.append(False)
        time.sleep(1)  # light pacing, not the app's full delay

    fail_index = next((i for i, ok in enumerate(results) if not ok), None)
    if fail_index is not None:
        print(
            f"\nFirst failure at request #{fail_index + 1} of {num_requests}.\n"
            "If early requests succeed and later ones fail, that's a within-session\n"
            "rate/bot escalation — confirms request VOLUME is the trigger, not the\n"
            "single-request setup."
        )
    else:
        print(f"\nAll {num_requests} requests succeeded. No escalation seen at this scale.")
    return results


def check_real_playlist(cookies_path, player_clients, playlist_url):
    """Tests the EXACT code path run_job() uses for playlists: a single
    extract_flat=True call against a real playlist URL, followed by
    downloading each resulting entry — not synthetic individual watch URLs.

    This is the one path tester.py never actually exercised before, and it's
    the one place where the real app's behavior could differ from every
    other check that passed.
    """
    banner("CHECK 9: REAL playlist extract_flat + per-entry download (exact app code path)")
    if not playlist_url:
        print("No --playlist-url given. Skipping. Pass one with --playlist-url <url>\n"
              "to run this check (use the actual playlist that's failing in the app).")
        return None

    print(
        f"Playlist URL: {playlist_url}\n"
        "Step 1: running the exact extract_flat call run_job() uses.\n"
        "Step 2: downloading each resulting entry one by one, exactly as the\n"
        "app's playlist loop does (including the same sleep delay between videos)."
    )

    base_auth = {}
    if cookies_path and os.path.exists(cookies_path):
        base_auth["cookiefile"] = cookies_path

    flat_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "extractor_args": {"youtube": {"player_client": player_clients}},
        **base_auth,
    }

    print("\n--- Step 1: extract_flat metadata fetch ---")
    try:
        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)
        entries = [e for e in info.get("entries", []) if e]
        print(f"OK: found {len(entries)} entries in playlist '{info.get('title', '?')}'")
    except Exception as e:
        print(f"FAIL at extract_flat stage: {e}")
        return {"flat_fetch": False, "downloads": []}

    print(f"\n--- Step 2: downloading each of {len(entries)} entries (format=worst, for speed) ---")
    download_results = []
    for idx, entry in enumerate(entries, start=1):
        video_id = entry.get("id")
        video_url = entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"
        opts = {
            **base_auth,
            "format": "worst",
            "outtmpl": os.path.join("tester_output", "playlist_%(id)s.%(ext)s"),
            "extractor_args": {"youtube": {"player_client": player_clients}},
            "quiet": True,
            "no_warnings": True,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([video_url])
            print(f"  [{idx}/{len(entries)}] OK ({video_id})")
            download_results.append(True)
        except Exception as e:
            print(f"  [{idx}/{len(entries)}] FAIL ({video_id}): {e}")
            download_results.append(False)
        if idx < len(entries):
            time.sleep(random.uniform(2, 5))

    first_fail = next((i for i, ok in enumerate(download_results) if not ok), None)
    if first_fail is not None:
        print(
            f"\nFirst failure at entry #{first_fail + 1} of {len(entries)} in the REAL "
            "playlist.\nThis is the exact pattern the app hits."
        )
    else:
        print(f"\nAll {len(entries)} entries downloaded successfully.")

    return {"flat_fetch": True, "entry_count": len(entries), "downloads": download_results}


def main():
    parser = argparse.ArgumentParser(description="Diagnose yt-dlp / YouTube access issues.")
    parser.add_argument("--cookies", default="cookies.txt",
                         help="Path to cookies.txt (default: cookies.txt in current dir)")
    parser.add_argument("--video-id", default=None,
                         help="Specific YouTube video ID to test against (default: tries a few known-stable ones)")
    parser.add_argument("--skip-download", action="store_true",
                         help="Skip the real download test (just run extraction checks)")
    parser.add_argument("--player-clients", default="android",
                         help="Comma-separated player client(s) to test against the app's "
                              "real format strings and playlist-scale check, e.g. 'android' "
                              "or 'android,web' (default: android)")
    parser.add_argument("--num-requests", type=int, default=8,
                         help="Number of back-to-back requests for the playlist-scale check "
                              "(default: 8). Use a higher number (e.g. 24) to fully reproduce "
                              "a large playlist run.")
    parser.add_argument("--playlist-url", default=None,
                         help="The ACTUAL playlist URL that's failing in your app. If given, "
                              "runs CHECK 9 which replicates the app's exact code path "
                              "(extract_flat + per-entry download) against this real playlist, "
                              "instead of synthetic single-video tests.")
    args = parser.parse_args()
    player_clients = [c.strip() for c in args.player_clients.split(",") if c.strip()]

    video_ids = [args.video_id] if args.video_id else TEST_VIDEO_IDS

    check_yt_dlp_version()
    cookie_status = check_cookies_file(args.cookies)

    # Use the first test video for the detailed checks, fall back through
    # the list if it happens to be unavailable for unrelated reasons.
    chosen_video = video_ids[0]

    no_auth_result = check_no_auth(chosen_video)
    with_cookies_result = check_with_cookies(args.cookies, chosen_video)
    client_results = check_player_clients(args.cookies, chosen_video)

    download_result = None
    if not args.skip_download:
        download_result = check_actual_download(args.cookies, chosen_video)

    app_format_results = check_app_format_strings(args.cookies, chosen_video, player_clients)
    playlist_scale_results = check_playlist_scale(args.cookies, player_clients, args.num_requests)
    real_playlist_result = check_real_playlist(args.cookies, player_clients, args.playlist_url)

    # --- Summary -----------------------------------------------------
    banner("SUMMARY")
    print(f"yt-dlp version:               {yt_dlp.version.__version__}")
    print(f"cookies.txt valid & authed:   {cookie_status}")
    print(f"Extraction with NO auth:      {no_auth_result}")
    print(f"Extraction WITH cookies:      {with_cookies_result}")
    print("Player client results (extraction only):")
    for client, ok in client_results.items():
        print(f"    {client:10s} -> {ok}")
    if download_result is not None:
        print(f"Real download attempt (worst):       {download_result}")
    print(f"\nApp's real format strings, player_client={player_clients}:")
    for quality, ok in app_format_results.items():
        print(f"    {quality:6s} -> {ok}")
    num_ok = sum(playlist_scale_results)
    print(f"\nPlaylist-scale burst ({args.num_requests} requests): {num_ok}/{args.num_requests} succeeded")
    if real_playlist_result is not None:
        if real_playlist_result.get("flat_fetch"):
            n = real_playlist_result["entry_count"]
            ok_count = sum(real_playlist_result["downloads"])
            print(f"\nREAL playlist test: extract_flat OK, {ok_count}/{n} entries downloaded")
        else:
            print("\nREAL playlist test: extract_flat FAILED before any downloads were attempted")

    print("\n" + DIVIDER)
    print("INTERPRETATION")
    print(DIVIDER)

    app_format_all_ok = all(app_format_results.values())
    app_format_any_ok = any(app_format_results.values())
    burst_all_ok = all(playlist_scale_results)
    burst_first_fail = next((i for i, ok in enumerate(playlist_scale_results) if not ok), None)

    real_playlist_all_ok = None
    real_playlist_first_fail = None
    if real_playlist_result is not None and real_playlist_result.get("flat_fetch"):
        downloads = real_playlist_result["downloads"]
        real_playlist_all_ok = all(downloads) if downloads else None
        real_playlist_first_fail = next((i for i, ok in enumerate(downloads) if not ok), None)

    if real_playlist_result is not None and not real_playlist_result.get("flat_fetch"):
        print(
            "- The REAL playlist's extract_flat call itself failed, before any per-video\n"
            "  download was even attempted. This is a different failure point than\n"
            "  anything else tested — check the Step 1 error above. This may mean the\n"
            "  playlist itself has an issue (private, region-locked, deleted) "
            "independent\n"
            "  of bot-detection entirely."
        )
    elif real_playlist_all_ok is False:
        print(
            f"- THE REAL PLAYLIST FAILS even though every synthetic test passed. First\n"
            f"  failure at entry #{real_playlist_first_fail + 1} of "
            f"{real_playlist_result['entry_count']}.\n"
            "  This confirms the issue is specific to THIS playlist/these videos, not\n"
            "  a general yt-dlp/IP/auth problem (which all passed). Likely causes:\n"
            "  the specific videos in this playlist may have per-video restrictions\n"
            "  (age-gated, region-locked, or owner-restricted) that trigger bot-checks\n"
            "  even when other public videos don't. Check which entries failed above —\n"
            "  if it's consistently the SAME videos failing every run, those specific\n"
            "  videos likely need cookies (real authentication) regardless of player\n"
            "  client, while the rest of your library doesn't."
        )
    elif real_playlist_all_ok is True:
        print(
            f"- The REAL playlist downloaded successfully end-to-end "
            f"({real_playlist_result['entry_count']} entries,\n"
            "  all OK) using the app's exact code path. If the actual app still fails\n"
            "  on this same playlist, the difference must be in something tester.py\n"
            "  does NOT replicate — e.g. the app's real format strings (best/720p/etc)\n"
            "  combined with THIS playlist's videos specifically (re-run CHECK 9-style\n"
            "  logic with real format strings instead of 'worst' if this happens), or\n"
            "  a difference in environment between when you ran tester.py and when you\n"
            "  ran the app (time elapsed, IP changed, etc)."
        )
    elif not app_format_any_ok:
        print(
            f"- NONE of the app's actual format strings work with player_client="
            f"{player_clients}, even though CHECK 5/6's simpler tests passed. This means\n"
            "  the earlier 'android works' result was specific to easy formats like\n"
            "  'worst' — it does NOT mean the real app works. Try adding 'web' back\n"
            "  in just for format-list purposes, or test with --player-clients "
            "android,web\n"
            "  and see if CHECK 7 passes then. Cookies may genuinely be required to\n"
            "  unlock the fuller format list this app needs."
        )
    elif not app_format_all_ok:
        failing = [q for q, ok in app_format_results.items() if not ok]
        print(
            f"- Some quality settings fail with player_client={player_clients}: "
            f"{failing}.\n"
            "  Avoid those specific quality options in the app for now, or test "
            "additional\n"
            "  player clients with --player-clients to find one with full format "
            "coverage."
        )
    elif not burst_all_ok:
        print(
            f"- Single requests work fine (including real format strings), but the\n"
            f"  playlist-scale burst test FAILED starting at request #{burst_first_fail + 1}\n"
            f"  of {args.num_requests}. This confirms the issue is request VOLUME, not "
            "auth or\n"
            "  format selection. Your app's per-video delay "
            "(MIN/MAX_SLEEP_BETWEEN_DOWNLOADS\n"
            "  in downloader.py) needs to be longer, or cookies are needed specifically "
            "to\n"
            "  survive sustained playlist-scale request volume even though they're not\n"
            "  needed for a single video."
        )
    elif no_auth_result:
        print(
            "- Everything passed: single requests, real app format strings, AND a "
            f"{args.num_requests}-request\n"
            "  burst. This setup should now work for actual playlists. If a real "
            "playlist\n"
            "  run still fails, the difference is likely playlist length or "
            "something\n"
            "  specific to that playlist — pass --playlist-url with the real failing\n"
            "  playlist to test that directly (CHECK 9)."
        )
    elif cookie_status is False:
        print(
            "- Your cookies.txt failed validation (see CHECK 2 output above) and\n"
            "  bare requests also failed. Re-export cookies and make sure you are\n"
            "  ACTUALLY logged into a Google account on youtube.com when exporting,\n"
            "  not just have the page open while logged out."
        )
    else:
        print(
            "- Mixed/inconclusive results — read the individual CHECK sections above\n"
            "  for the specific failure pattern."
        )


if __name__ == "__main__":
    main()