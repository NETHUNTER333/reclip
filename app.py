import os
import uuid
import glob
import json
import subprocess
import threading
import re

from flask import Flask, request, jsonify, send_file, render_template
from youtube_transcript_api import YouTubeTranscriptApi

app = Flask(__name__)

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

jobs = {}

# -------------------------------
# Shared yt-dlp fallback helper
# -------------------------------

def run_ytdlp_with_fallback(base_cmd, timeout=300):
    clients = ["android", "tv_embedded", "web"]
    errors = []

    for client in clients:
        cmd = base_cmd.copy()

        inject = [
 "--cookies",
 "/etc/secrets/cookies.txt",

 "--extractor-args",
 f"youtube:player_client={client};player_skip=webpage",

 "--user-agent",
 "com.google.android.youtube/17.31.35 (Linux; U; Android 11)",

 "--js-runtimes","deno",

 "--sleep-requests","5",
 "--retries","15",
 "--fragment-retries","15"
],

        cmd = [cmd[0]] + inject + cmd[1:]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            if result.returncode == 0:
                return result, client

            errors.append(f"{client}: {result.stderr.strip()}")

        except Exception as e:
            errors.append(f"{client}: {str(e)}")

    raise Exception("All clients failed:\n" + "\n".join(errors))


# -------------------------------
# Helpers
# -------------------------------

def extract_video_id(url):
    patterns = [
        r"v=([^&]+)",
        r"youtu\.be/([^?]+)"
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


# -------------------------------
# Download worker
# -------------------------------

def run_download(job_id, url, format_choice, format_id):
    job = jobs[job_id]

    out_template = os.path.join(
        DOWNLOAD_DIR,
        f"{job_id}.%(ext)s"
    )

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-o", out_template
    ]

    if format_choice == "audio":
        cmd += [
            "-x",
            "--audio-format",
            "mp3"
        ]

    elif format_id:
        cmd += [
            "-f",
            f"{format_id}+bestaudio/best",
            "--merge-output-format",
            "mp4"
        ]

    else:
        cmd += [
            "-f",
            "bestvideo+bestaudio/best",
            "--merge-output-format",
            "mp4"
        ]

    cmd.append(url)

    try:
        result, client_used = run_ytdlp_with_fallback(
            cmd,
            timeout=300
        )

        job["client_used"] = client_used

        files = glob.glob(
            os.path.join(
                DOWNLOAD_DIR,
                f"{job_id}.*"
            )
        )

        if not files:
            job["status"] = "error"
            job["error"] = "Download finished but no file found"
            return

        if format_choice == "audio":
            mp3s = [f for f in files if f.endswith(".mp3")]
            chosen = mp3s[0] if mp3s else files[0]
        else:
            mp4s = [f for f in files if f.endswith(".mp4")]
            chosen = mp4s[0] if mp4s else files[0]

        for f in files:
            if f != chosen:
                try:
                    os.remove(f)
                except:
                    pass

        job["status"] = "done"
        job["file"] = chosen

        ext = os.path.splitext(chosen)[1]

        title = job.get("title", "").strip()

        if title:
            safe_title = "".join(
                c for c in title
                if c not in r'\/:*?"<>|'
            ).strip()[:20]

            if safe_title:
                job["filename"] = safe_title + ext
            else:
                job["filename"] = os.path.basename(chosen)

        else:
            job["filename"] = os.path.basename(chosen)

    except subprocess.TimeoutExpired:
        job["status"] = "error"
        job["error"] = "Download timed out"

    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


# -------------------------------
# Routes
# -------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.json
    url = data.get("url", "").strip()

    if not url:
        return jsonify({
            "error": "No URL provided"
        }), 400

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-j",
        url
    ]

    try:
        result, client_used = run_ytdlp_with_fallback(
            cmd,
            timeout=60
        )

        info = json.loads(result.stdout)

        best_by_height = {}

        for f in info.get("formats", []):
            height = f.get("height")

            if height and f.get("vcodec", "none") != "none":
                tbr = f.get("tbr") or 0

                if (
                    height not in best_by_height or
                    tbr > (
                        best_by_height[height].get("tbr") or 0
                    )
                ):
                    best_by_height[height] = f

        formats = []

        for height, f in best_by_height.items():
            formats.append({
                "id": f["format_id"],
                "label": f"{height}p",
                "height": height
            })

        formats.sort(
            key=lambda x: x["height"],
            reverse=True
        )

        return jsonify({
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration"),
            "uploader": info.get("uploader", ""),
            "formats": formats,
            "client_used": client_used
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 400


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json

    url = data.get("url", "").strip()
    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")

    if not url:
        return jsonify({
            "error": "No URL provided"
        }), 400

    job_id = uuid.uuid4().hex[:10]

    jobs[job_id] = {
        "status": "downloading",
        "url": url,
        "title": title
    }

    t = threading.Thread(
        target=run_download,
        args=(
            job_id,
            url,
            format_choice,
            format_id
        )
    )

    t.daemon = True
    t.start()

    return jsonify({
        "job_id": job_id
    })


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)

    if not job:
        return jsonify({
            "error": "Job not found"
        }), 404

    return jsonify({
        "status": job["status"],
        "error": job.get("error"),
        "filename": job.get("filename"),
        "client_used": job.get("client_used")
    })


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)

    if not job or job["status"] != "done":
        return jsonify({
            "error": "File not ready"
        }), 404

    return send_file(
        job["file"],
        as_attachment=True,
        download_name=job["filename"]
    )


@app.route("/api/transcript", methods=["POST"])
def get_transcript():
    data = request.json
    url = data.get("url", "").strip()

    if not url:
        return jsonify({
            "error": "No URL provided"
        }), 400

    video_id = extract_video_id(url)

    if not video_id:
        return jsonify({
            "error": "Invalid YouTube URL"
        }), 400

    try:
        try:
            transcript = YouTubeTranscriptApi.get_transcript(
                video_id,
                languages=["en", "hi"]
            )
        except:
            transcript = YouTubeTranscriptApi.get_transcript(
                video_id
            )

        text = " ".join(
            dict.fromkeys(
                x["text"] for x in transcript
            )
        )

        text = text[:5000]

        return jsonify({
            "transcript": text
        })

    except:
        return jsonify({
            "error":
            "Transcript not available for this video"
        }), 400


if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", 8899)
    )

    host = os.environ.get(
        "HOST",
        "127.0.0.1"
    )

    app.run(
        host=host,
        port=port
    )