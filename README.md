# YTdvy

A local web app: paste a YouTube video or playlist link, and it downloads it.
Playlists are downloaded clip-by-clip and then spliced into a single merged
video file, in playlist order.

This is built for **personal, local use only** — it is not meant to be
deployed publicly, and downloading copyrighted content you don't have rights
to may violate YouTube's Terms of Service depending on how you use it.
That's on you to use responsibly.

## What it does

- Paste any YouTube link — video or playlist, desktop or mobile share format.
- The app normalizes the link (strips tracking junk like `?si=`, handles
  `youtu.be`, `m.youtube.com`, `shorts/`, missing `https://`, etc.) and
  figures out if it's a single video or a playlist.
- **Single video** → downloads it as one file.
- **Playlist** → downloads every video in it, then uses `ffmpeg` to merge
  them into one continuous video file, in playlist order.
- Pick quality: best available, 1080p, 720p, 480p, or audio-only (mp3).

## DEPLOYED AT 

https://ytdvy-youtubeplaylistdownload.onrender.com/

## Requirements

- Python 3.9+
- `ffmpeg` installed and on your system `PATH`
  - Mac: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: download from https://ffmpeg.org/download.html and add to PATH

## Setup

```bash
cd ytdvy
pip install -r requirements.txt
```

## Run

```bash
python3 app.py
```

Then open **http://127.0.0.1:5000** in your browser.

## Usage

1. Paste a YouTube video or playlist URL into the input box.
2. A small tag next to the box tells you whether it detected a **single
   video** or a **playlist**.
3. Pick a quality (BEST / 1080p / 720p / 480p / AUDIO).
4. Click **PULL**.
5. Watch the progress bar. For playlists, it'll show "x / y clips" while
   downloading, then switch to "merging" while ffmpeg splices them together.
6. When done, a download link appears — click it to save the final file.

Downloaded files are also kept in the `downloads/` folder inside the project
directory, in case you want to grab them directly from disk.

## Notes on how merging works

- Videos are merged using ffmpeg's `concat` demuxer.
- It first tries a fast stream-copy merge (no quality loss, very fast) —
  this works when all videos in the playlist share the same codec/resolution
  (common if they're all from the same channel/source).
- If that fails (mismatched resolutions/codecs across videos), it
  automatically falls back to re-encoding everything to a common format
  before merging. This is slower but handles mixed-quality playlists.
- If a video in a playlist is unavailable (private, deleted, region-locked),
  it's skipped automatically and the rest of the playlist continues.

## Project structure

```
ytdvy/
├── app.py            Flask routes (UI, job submission, status, downloads)
├── downloader.py      yt-dlp + ffmpeg logic, runs jobs in background threads
├── url_utils.py        YouTube URL normalization/classification
├── requirements.txt
├── templates/
│   └── index.html
├── static/
│   ├── style.css
│   └── app.js
├── downloads/         Final output files land here
└── temp/              Scratch space per job, auto-cleaned after each run
```

## Troubleshooting

- **"ffmpeg: command not found"** — install ffmpeg (see Requirements above)
  and make sure it's on your PATH (`ffmpeg -version` should work in a terminal).
- **Downloads fail / "Sign in to confirm you're not a bot"** — YouTube
  occasionally rate-limits or challenges yt-dlp. Try updating it:
  `pip install -U yt-dlp` (YouTube changes things often; yt-dlp ships fixes
  frequently).
- **Some playlist videos get skipped** — that's intentional; private/deleted/
  region-locked videos are skipped so the rest of the playlist can still
  complete and merge.
- **Job seems stuck** — check the terminal running `python3 app.py` for the
  actual error; it'll be printed there even if the browser doesn't show much.
