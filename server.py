"""MOPD course server: phuc vu website, dang nhap va luu tien do hoc tap (SQLite)."""
import getpass
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("MOPD_DATA_DIR", BASE / "data"))
DB_PATH = DATA / "mopd.db"
STATIC = BASE / "static"
ALLOW_REGISTER = os.environ.get("MOPD_ALLOW_REGISTER", "1") == "1"
BEHIND_CLOUDFLARE = os.environ.get("MOPD_BEHIND_CLOUDFLARE", "0") == "1"
USER_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
DATA.mkdir(parents=True, exist_ok=True)


def _secret_key():
    key = os.environ.get("MOPD_SECRET_KEY")
    if key:
        return key
    f = DATA / "secret_key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        os.chmod(f, 0o600)
    return f.read_text().strip()


app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("MOPD_COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,      # dang nhap giu 30 ngay
    MAX_CONTENT_LENGTH=256 * 1024,
)


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                pw_hash TEXT NOT NULL,
                created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS progress(
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                data TEXT NOT NULL,
                updated_at REAL NOT NULL);
        """)


init_db()


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def _close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@app.after_request
def _headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- bao ve: chong CSRF va chong do mat khau ----------
def json_post(fn):
    """Yeu cau header rieng: form cua trang web khac khong gui duoc header nay (chong CSRF)."""
    @wraps(fn)
    def wrapper(*a, **kw):
        if request.headers.get("X-Requested-With") != "mopd":
            return jsonify(error="Yêu cầu không hợp lệ."), 400
        return fn(*a, **kw)
    return wrapper


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("uid"):
            return jsonify(error="Chưa đăng nhập."), 401
        return fn(*a, **kw)
    return wrapper


_fails = {}


def _client_ip():
    if BEHIND_CLOUDFLARE and request.headers.get("CF-Connecting-IP"):
        return request.headers["CF-Connecting-IP"]
    return request.remote_addr or "?"


def _too_many(ip, limit=10, window=300):
    now = time.time()
    _fails[ip] = [t for t in _fails.get(ip, []) if now - t < window]
    return len(_fails[ip]) >= limit


def _fail(ip):
    _fails.setdefault(ip, []).append(time.time())


_DUMMY_HASH = generate_password_hash("khong-phai-mat-khau-that")


def _creds():
    d = request.get_json(silent=True) or {}
    return str(d.get("username", "")).strip(), str(d.get("password", ""))


def _start_session(uid):
    session.clear()
    session["uid"] = uid
    session.permanent = True


# ---------- trang web ----------
@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


# ---------- tai khoan ----------
@app.get("/api/me")
def me():
    user = None
    uid = session.get("uid")
    if uid:
        row = db().execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        if row:
            user = {"username": row["username"]}
        else:
            session.clear()
    return jsonify(user=user, allow_register=ALLOW_REGISTER)


@app.post("/api/register")
@json_post
def register():
    if not ALLOW_REGISTER:
        return jsonify(error="Chức năng đăng ký đang tắt."), 403
    ip = _client_ip()
    if _too_many(ip):
        return jsonify(error="Thử quá nhiều lần, vui lòng đợi vài phút."), 429
    u, p = _creds()
    if not USER_RE.match(u):
        return jsonify(error="Tên đăng nhập 3–32 ký tự: chữ, số, dấu chấm, gạch dưới, gạch ngang."), 400
    if len(p) < 8 or len(p) > 128:
        return jsonify(error="Mật khẩu cần từ 8 tới 128 ký tự."), 400
    try:
        cur = db().execute("INSERT INTO users(username, pw_hash, created_at) VALUES(?,?,?)",
                           (u, generate_password_hash(p), time.time()))
        db().commit()
    except sqlite3.IntegrityError:
        _fail(ip)
        return jsonify(error="Tên đăng nhập đã tồn tại."), 409
    _start_session(cur.lastrowid)
    return jsonify(ok=True, user={"username": u})


@app.post("/api/login")
@json_post
def login():
    ip = _client_ip()
    if _too_many(ip):
        return jsonify(error="Thử quá nhiều lần, vui lòng đợi vài phút."), 429
    u, p = _creds()
    row = db().execute("SELECT id, username, pw_hash FROM users WHERE username=?", (u,)).fetchone()
    ok = check_password_hash(row["pw_hash"] if row else _DUMMY_HASH, p) and row is not None
    if not ok:
        _fail(ip)
        return jsonify(error="Sai tên đăng nhập hoặc mật khẩu."), 401
    _start_session(row["id"])
    return jsonify(ok=True, user={"username": row["username"]})


@app.post("/api/logout")
@json_post
def logout():
    session.clear()
    return jsonify(ok=True)


# ---------- tien do hoc tap ----------
def _clean_progress(d):
    if not isinstance(d, dict):
        raise ValueError("payload")
    comp = d.get("completed", [])
    if not isinstance(comp, list) or len(comp) > 2000:
        raise ValueError("completed")
    comp = sorted({c for c in comp if isinstance(c, str) and 0 < len(c) <= 64})
    cur = d.get("current")
    cur = cur if isinstance(cur, str) and len(cur) <= 64 else None
    quiz = d.get("quiz", {})
    quiz = quiz if isinstance(quiz, dict) else {}
    out = {"completed": comp, "current": cur, "quiz": quiz}
    if len(json.dumps(out)) > 200_000:
        raise ValueError("size")
    return out


@app.get("/api/progress")
@login_required
def get_progress():
    row = db().execute("SELECT data, updated_at FROM progress WHERE user_id=?", (session["uid"],)).fetchone()
    if not row:
        return jsonify(data=None)
    return jsonify(data=json.loads(row["data"]), updated_at=row["updated_at"])


@app.post("/api/progress")
@json_post
@login_required
def save_progress():
    try:
        data = _clean_progress(request.get_json(silent=True))
    except ValueError:
        return jsonify(error="Dữ liệu tiến độ không hợp lệ."), 400
    now = time.time()
    db().execute("INSERT INTO progress(user_id, data, updated_at) VALUES(?,?,?) "
                 "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                 (session["uid"], json.dumps(data, ensure_ascii=False), now))
    db().commit()
    return jsonify(ok=True, updated_at=now, completed=len(data["completed"]))


# ---------- dong lenh quan tri ----------
def _cli():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd in ("create-user", "reset-password") and len(sys.argv) == 3:
        u = sys.argv[2]
        if not USER_RE.match(u):
            sys.exit("Tên đăng nhập không hợp lệ.")
        p = getpass.getpass("Mật khẩu (>= 8 ký tự): ")
        if len(p) < 8 or p != getpass.getpass("Nhập lại: "):
            sys.exit("Mật khẩu quá ngắn hoặc không khớp.")
        with sqlite3.connect(DB_PATH) as c:
            if cmd == "create-user":
                try:
                    c.execute("INSERT INTO users(username, pw_hash, created_at) VALUES(?,?,?)",
                              (u, generate_password_hash(p), time.time()))
                except sqlite3.IntegrityError:
                    sys.exit("Tên đăng nhập đã tồn tại.")
            else:
                n = c.execute("UPDATE users SET pw_hash=? WHERE username=?", (generate_password_hash(p), u)).rowcount
                if not n:
                    sys.exit("Không tìm thấy người dùng.")
        print("Xong:", cmd, u)
    elif cmd == "list-users":
        with sqlite3.connect(DB_PATH) as c:
            for (name, created) in c.execute("SELECT username, created_at FROM users ORDER BY id"):
                print(name, time.strftime("%Y-%m-%d %H:%M", time.localtime(created)))
    elif cmd == "run":
        app.run(host=os.environ.get("MOPD_HOST", "127.0.0.1"), port=int(os.environ.get("MOPD_PORT", "8000")))
    else:
        print("Cách dùng: python server.py [run | create-user TEN | reset-password TEN | list-users]")


if __name__ == "__main__":
    _cli()
