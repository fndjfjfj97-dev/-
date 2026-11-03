from __future__ import annotations

import os
import secrets
import sqlite3
from functools import wraps
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
# On a hosting provider DATA_DIR can point to a persistent disk. Locally the
# project folder is used, so the site works immediately after unpacking.
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR))
UPLOAD_DIR = DATA_DIR / "uploads"
DATABASE = DATA_DIR / "broadcasts.db"
TIMEZONE = ZoneInfo("Europe/Chisinau")
ALLOWED_EXTENSIONS = {"mp4", "webm", "mov", "m4v"}

app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=900 * 1024 * 1024,  # Fits on the 1 GB Render disk.
    SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_urlsafe(32)),
)


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                title TEXT NOT NULL,
                start_at TEXT NOT NULL,
                video_filename TEXT NOT NULL,
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )


def current_broadcast() -> dict | None:
    with db() as connection:
        row = connection.execute(
            "SELECT title, start_at, video_filename FROM broadcast WHERE id = 1 AND active = 1"
        ).fetchone()
    if row is None:
        return None

    start = datetime.fromisoformat(row["start_at"]).replace(tzinfo=TIMEZONE)
    return {
        "title": row["title"],
        "startAt": row["start_at"],
        "startUnix": int(start.timestamp() * 1000),
        "videoUrl": url_for("uploaded_file", filename=row["video_filename"]),
    }


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def creator_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("creator_access"):
            return redirect(url_for("creator_login"))
        return view(*args, **kwargs)
    return wrapped


@app.get("/")
def home():
    return redirect(url_for("viewer"))


@app.get("/creator")
@creator_required
def creator():
    return render_template("creator.html", broadcast=current_broadcast())


@app.route("/creator/login", methods=["GET", "POST"])
def creator_login():
    password = os.environ.get("CREATOR_PASSWORD")
    if request.method == "POST":
        if password and secrets.compare_digest(request.form.get("password", ""), password):
            session.clear()
            session["creator_access"] = True
            return redirect(url_for("creator"))
        return render_template("login.html", error="Неверный пароль." if password else "Пароль автора ещё не настроен."), 401
    return render_template("login.html")


@app.post("/creator/logout")
@creator_required
def creator_logout():
    session.clear()
    return redirect(url_for("creator_login"))


@app.get("/watch")
def viewer():
    return render_template("viewer.html")


@app.get("/api/broadcast")
def broadcast_api():
    return jsonify({"broadcast": current_broadcast(), "serverUnix": int(datetime.now(TIMEZONE).timestamp() * 1000)})


@app.get("/media/<path:filename>")
def uploaded_file(filename: str):
    # send_from_directory blocks path traversal and exposes only uploaded files.
    return send_from_directory(UPLOAD_DIR, filename)


@app.post("/creator/schedule")
@creator_required
def schedule():
    title = (request.form.get("title") or "Эфир Канала Бурмалды").strip()[:80]
    start_at = request.form.get("start_at", "").strip()
    video = request.files.get("video")

    try:
        start = datetime.fromisoformat(start_at).replace(tzinfo=TIMEZONE)
    except ValueError:
        return render_template("creator.html", broadcast=current_broadcast(), error="Укажите дату и время начала."), 400

    if start <= datetime.now(TIMEZONE):
        return render_template("creator.html", broadcast=current_broadcast(), error="Время начала должно быть в будущем."), 400
    if not video or not video.filename:
        return render_template("creator.html", broadcast=current_broadcast(), error="Выберите видео для эфира."), 400
    if not allowed_file(video.filename):
        return render_template("creator.html", broadcast=current_broadcast(), error="Подойдут MP4, WebM, MOV или M4V."), 400

    safe_name = secure_filename(video.filename)
    unique_name = f"{secrets.token_hex(10)}_{safe_name}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    video.save(UPLOAD_DIR / unique_name)

    with db() as connection:
        connection.execute(
            """
            INSERT INTO broadcast (id, title, start_at, video_filename, created_at, active)
            VALUES (1, ?, ?, ?, ?, 1)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                start_at=excluded.start_at,
                video_filename=excluded.video_filename,
                created_at=excluded.created_at,
                active=1
            """,
            (title, start_at, unique_name, datetime.now(TIMEZONE).isoformat()),
        )
    return redirect(url_for("creator", saved="1"))


@app.post("/creator/cancel")
@creator_required
def cancel():
    with db() as connection:
        connection.execute("UPDATE broadcast SET active = 0 WHERE id = 1")
    return redirect(url_for("creator", cancelled="1"))


@app.errorhandler(413)
def file_too_large(_error):
    return render_template("creator.html", broadcast=current_broadcast(), error="Файл больше 900 МБ. Для большего видео увеличьте диск в Render."), 413


init_db()

if __name__ == "__main__":
    # host=0.0.0.0 makes the viewer page available to phones on the same Wi‑Fi.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
