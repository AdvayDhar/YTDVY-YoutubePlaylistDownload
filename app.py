"""
YTdvy - local Flask app.

Paste a YouTube video or playlist link:
  - Video link      -> downloads that single video.
  - Playlist link   -> downloads every video in the playlist and merges
                        them into a single output file.

Run with:  python3 app.py
Then open: http://127.0.0.1:5000
"""
import os
import threading

from flask import Flask, render_template, request, jsonify, send_from_directory

from url_utils import normalize_youtube_url, LinkType
from downloader import create_job, run_job, JOBS, DOWNLOADS_DIR

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/check-url", methods=["POST"])
def check_url():
    """Lets the frontend show 'Video detected' / 'Playlist detected' before submitting."""
    data = request.get_json(silent=True) or {}
    raw_url = data.get("url", "")
    result = normalize_youtube_url(raw_url)
    return jsonify(result)


@app.route("/api/submit", methods=["POST"])
def submit():
    data = request.get_json(silent=True) or {}
    raw_url = data.get("url", "")
    quality = data.get("quality", "best")

    norm = normalize_youtube_url(raw_url)
    if norm["type"] == LinkType.INVALID:
        return jsonify({"error": norm["error"] or "Invalid YouTube link."}), 400

    job = create_job(norm["type"], quality)
    thread = threading.Thread(target=run_job, args=(job, raw_url), daemon=True)
    thread.start()

    return jsonify({"job_id": job.id})


@app.route("/api/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job id."}), 404
    return jsonify(job.to_dict())


@app.route("/downloads/<path:filename>")
def download_file(filename):
    return send_from_directory(DOWNLOADS_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    print("=" * 50)
    print("  YTdvy running at http://127.0.0.1:5000")
    print("=" * 50)
    app.run(host="127.0.0.1", port=5000, debug=False)
