# app.py — улучшенный файл с новыми функциями, дизайном, анимациями, фиксами багов и соглашением
import os
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta
from flask import Flask, request, redirect, session, send_from_directory, jsonify, render_template_string, url_for
from werkzeug.utils import secure_filename
from gtts import gTTS
import requests  # для скачивания по ссылке

# ========== Конфигурация ==========
APP_SECRET = "supersecretkey_replace"        # поменяй на свой секрет
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "12we1qtr11"                # можешь поменять
ALLOWED_EXT = {'.mp3'}
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
MUSIC_FOLDER = os.path.join(BASE_DIR, "music")
VOICE_FOLDER = os.path.join(BASE_DIR, "voices")
DB_PATH = os.path.join(BASE_DIR, "radio.db")

os.makedirs(MUSIC_FOLDER, exist_ok=True)
os.makedirs(VOICE_FOLDER, exist_ok=True)

# ========== Flask ==========
app = Flask(__name__)
app.secret_key = APP_SECRET

# ========== БД: helper ==========
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    c = db.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            banned INTEGER DEFAULT 0,
            last_seen TEXT DEFAULT NULL,
            agreed_to_terms INTEGER DEFAULT 0,
            agreed_to_privacy INTEGER DEFAULT 0,
            agreed_to_data INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            display_name TEXT,
            uploaded_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS state (
            id INTEGER PRIMARY KEY CHECK (id=1),
            current_track_id INTEGER,
            current_display_name TEXT,
            volume REAL DEFAULT 0.6,
            FOREIGN KEY (current_track_id) REFERENCES tracks(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY,
            track_id INTEGER,
            username TEXT,
            liked_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY,
            username TEXT,
            reason TEXT,
            track_id INTEGER,
            created_at TEXT,
            processed INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY,
            username TEXT,
            track_id INTEGER,
            requested_at TEXT,
            approved INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY,
            username TEXT,
            message TEXT,
            created_at TEXT,
            deleted INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS add_track_requests (
            id INTEGER PRIMARY KEY,
            username TEXT,
            url_or_file TEXT,
            display_name TEXT,
            requested_at TEXT,
            approved INTEGER DEFAULT 0,
            rejected INTEGER DEFAULT 0
        )
    """)
    c.execute("INSERT OR IGNORE INTO state (id, volume) VALUES (1, 0.6)")
    db.commit()
    db.close()

# миграция
def migrate_db():
    conn = get_db()
    cur = conn.cursor()
    # users additional agreements
    cur.execute("PRAGMA table_info(users)")
    cols = {r['name'] for r in cur.fetchall()}
    if 'agreed_to_privacy' not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN agreed_to_privacy INTEGER DEFAULT 0")
    if 'agreed_to_data' not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN agreed_to_data INTEGER DEFAULT 0")
    # chat deleted
    cur.execute("PRAGMA table_info(chat_messages)")
    cols = {r['name'] for r in cur.fetchall()}
    if 'deleted' not in cols:
        cur.execute("ALTER TABLE chat_messages ADD COLUMN deleted INTEGER DEFAULT 0")
    # add_track_requests
    cur.execute("CREATE TABLE IF NOT EXISTS add_track_requests (id INTEGER PRIMARY KEY, username TEXT, url_or_file TEXT, display_name TEXT, requested_at TEXT, approved INTEGER DEFAULT 0, rejected INTEGER DEFAULT 0)")
    conn.commit()
    conn.close()

init_db()
migrate_db()

# ========== Утилиты ==========
def is_allowed(filename):
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXT

def validate_registration(username, password):
    if not username or not password:
        return "Заполните поля."
    if len(username) < 4:
        return "Имя пользователя должно быть не короче 4 символов."
    if len(password) < 8:
        return "Пароль должен быть не менее 8 символов."
    if not re.search(r'[A-Za-zА-Яа-я]', password) or not re.search(r'\d', password):
        return "Пароль должен содержать хотя бы одну букву и одну цифру."
    if not re.match(r'^[A-Za-zА-Яа-я0-9_]+$', username):
        return "Ник может содержать только буквы, цифры и подчёркивание."
    if username.lower() == 'admin':
        return "Запрещённый ник."
    return None

def log_action(level, message):
    try:
        with open(os.path.join(BASE_DIR, "radio.log"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.utcnow().isoformat()}] {level.upper()}: {message}\n")
    except:
        pass

# ========== Голосовое приветствие ==========
def generate_greeting(display_name, radio_name="Радио Фонтан"):
    text = f"Вас приветствует {radio_name}. Сейчас играет: {display_name}. Приятного прослушивания!"
    t = int(time.time() * 1000)
    safe_name = f"greeting_{t}.mp3"
    path = os.path.join(VOICE_FOLDER, safe_name)
    try:
        tts = gTTS(text=text, lang='ru')
        tts.save(path)
    except Exception as e:
        log_action("error", f"gTTS failed: {e}")
        safe_name = "greeting_empty.mp3"
        path = os.path.join(VOICE_FOLDER, safe_name)
        if not os.path.exists(path):
            open(path, "wb").close()
    try:
        files = [f for f in os.listdir(VOICE_FOLDER) if f.startswith("greeting_")]
        files_sorted = sorted(files, key=lambda x: os.path.getmtime(os.path.join(VOICE_FOLDER, x)), reverse=True)
        for old in files_sorted[20:]:
            os.remove(os.path.join(VOICE_FOLDER, old))
    except:
        pass
    return safe_name

# ========== state helpers ==========
def set_current_track(track_id, display_name=None):
    conn = get_db()
    c = conn.cursor()
    if display_name is None:
        row = c.execute("SELECT display_name FROM tracks WHERE id = ?", (track_id,)).fetchone()
        display_name = row['display_name'] if row else ''
    c.execute("UPDATE state SET current_track_id = ?, current_display_name = ? WHERE id = 1",
              (track_id, display_name))
    log_action("info", f"Set current track {track_id} ({display_name})")
    conn.commit()
    conn.close()
    greeting_file = generate_greeting(display_name)
    return greeting_file

def get_state():
    conn = get_db()
    c = conn.cursor()
    row = c.execute("SELECT * FROM state WHERE id = 1").fetchone()
    conn.close()
    return row

# ========== Auth / Register ==========
@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        agreed_terms = request.form.get('agreed_to_terms') == 'on'
        agreed_privacy = request.form.get('agreed_to_privacy') == 'on'
        agreed_data = request.form.get('agreed_to_data') == 'on'
        error = validate_registration(username, password)
        if not agreed_terms or not agreed_privacy or not agreed_data:
            error = "Вы должны согласиться со всеми документами."
        if error:
            pass
        else:
            try:
                conn = get_db()
                conn.execute("INSERT INTO users (username, password, agreed_to_terms, agreed_to_privacy, agreed_to_data, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                             (username, password, 1, 1, 1, datetime.utcnow().isoformat()))
                conn.commit()
                conn.close()
                log_action("info", f"New user registered: {username}")
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                error = "Пользователь с таким именем уже существует."
    return render_template_string(REG_TEMPLATE_REGISTER, error=error)

@app.route('/', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['username'] = username
            session['is_admin'] = True
            return redirect('/admin')
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password)).fetchone()
        if row:
            banned = row['banned']
            if banned:
                conn.close()
                return render_template_string(REG_TEMPLATE_LOGIN, error="Ваш аккаунт заблокирован.")
            session['username'] = username
            session['is_admin'] = False
            conn.execute("UPDATE users SET last_seen = ? WHERE username = ?", (datetime.utcnow().isoformat(), username))
            conn.commit()
            conn.close()
            return redirect('/radio')
        else:
            conn.close()
            error = "Неверный логин или пароль."
    return render_template_string(REG_TEMPLATE_LOGIN, error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ========== Статические ==========
@app.route('/music/<path:filename>')
def music_file(filename):
    return send_from_directory(MUSIC_FOLDER, filename)

@app.route('/voice/<path:filename>')
def voice_file(filename):
    return send_from_directory(VOICE_FOLDER, filename)

# ========== Middleware ==========
def admin_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get('is_admin'):
            return redirect(url_for('login'))
        return fn(*a, **kw)
    return wrapper

def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*a, **kw):
        if 'username' not in session:
            return redirect(url_for('login'))
        return fn(*a, **kw)
    return wrapper

# ========== Admin panel ==========
@app.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin_panel():
    message = None
    if request.method == 'POST':
        if 'track' in request.files:
            f = request.files['track']
            if f and is_allowed(f.filename):
                filename = secure_filename(f.filename)
                dest = os.path.join(MUSIC_FOLDER, filename)
                if os.path.exists(dest):
                    base, ext = os.path.splitext(filename)
                    filename = f"{base}_{random.randint(1000,9999)}{ext}"
                    dest = os.path.join(MUSIC_FOLDER, filename)
                try:
                    f.save(dest)
                    display_name = request.form.get('display_name') or filename
                    conn = get_db()
                    conn.execute("INSERT INTO tracks (filename, display_name, uploaded_at) VALUES (?, ?, ?)",
                                 (filename, display_name, datetime.utcnow().isoformat()))
                    conn.commit()
                    conn.close()
                    message = "Трек загружен."
                    log_action("info", f"Admin uploaded track {filename} ({display_name})")
                except Exception as e:
                    message = "Ошибка при сохранении файла."
                    log_action("error", f"Upload failed: {e}")
            else:
                message = "Неверный файл (только .mp3)."
        if request.form.get('delete_id'):
            try:
                tid = int(request.form.get('delete_id'))
                conn = get_db()
                row = conn.execute("SELECT filename FROM tracks WHERE id = ?", (tid,)).fetchone()
                if row:
                    fname = row['filename']
                    path = os.path.join(MUSIC_FOLDER, fname)
                    if os.path.exists(path):
                        os.remove(path)
                    conn.execute("DELETE FROM tracks WHERE id = ?", (tid,))
                    conn.execute("DELETE FROM likes WHERE track_id = ?", (tid,))
                    conn.execute("DELETE FROM reports WHERE track_id = ?", (tid,))
                    conn.execute("DELETE FROM requests WHERE track_id = ?", (tid,))
                    conn.commit()
                    message = "Трек удалён."
                    log_action("info", f"Admin deleted track {fname}")
                conn.close()
            except Exception as e:
                log_action("error", f"Delete track error: {e}")
        if request.form.get('set_current'):
            try:
                tid = int(request.form.get('set_current'))
                display = request.form.get('current_display') or None
                greeting = set_current_track(tid, display)
                message = f"Текущий трек установлен. Приветствие: {greeting}"
            except Exception as e:
                message = "Ошибка установки текущего трека."
                log_action("error", f"Set current error: {e}")
        if request.form.get('set_volume'):
            try:
                v = float(request.form.get('set_volume'))
                conn = get_db()
                conn.execute("UPDATE state SET volume = ? WHERE id = 1", (max(0, min(1, v)),))
                conn.commit()
                conn.close()
                message = "Громкость сохранена."
            except:
                message = "Неверное значение громкости."
        if request.form.get('ban_user'):
            uname = request.form.get('ban_user')
            conn = get_db()
            conn.execute("UPDATE users SET banned = 1 WHERE username = ?", (uname,))
            conn.commit()
            conn.close()
            message = f"Пользователь {uname} заблокирован."
            log_action("warn", f"Admin banned {uname}")
        if request.form.get('unban_user'):
            uname = request.form.get('unban_user')
            conn = get_db()
            conn.execute("UPDATE users SET banned = 0 WHERE username = ?", (uname,))
            conn.commit()
            conn.close()
            message = f"Пользователь {uname} разблокирован."
            log_action("info", f"Admin unbanned {uname}")
        if request.form.get('process_report'):
            rid = int(request.form.get('process_report'))
            conn = get_db()
            conn.execute("UPDATE reports SET processed = 1 WHERE id = ?", (rid,))
            conn.commit()
            conn.close()
            message = "Жалоба отмечена как обработанной."
        if request.form.get('approve_request'):
            req_id = int(request.form.get('approve_request'))
            conn = get_db()
            row = conn.execute("SELECT track_id FROM requests WHERE id = ?", (req_id,)).fetchone()
            if row:
                set_current_track(row['track_id'])
                conn.execute("UPDATE requests SET approved = 1 WHERE id = ?", (req_id,))
                conn.commit()
                message = "Запрос одобрен и трек установлен."
            conn.close()
        if request.form.get('approve_add_request'):
            req_id = int(request.form.get('approve_add_request'))
            conn = get_db()
            row = conn.execute("SELECT url_or_file, display_name FROM add_track_requests WHERE id = ?", (req_id,)).fetchone()
            if row:
                url_or_file = row['url_or_file']
                display_name = row['display_name']
                if url_or_file.startswith('http'):  # скачивание по ссылке
                    try:
                        response = requests.get(url_or_file)
                        if response.status_code == 200:
                            filename = secure_filename(url_or_file.split('/')[-1])
                            if not is_allowed(filename):
                                filename += '.mp3'
                            dest = os.path.join(MUSIC_FOLDER, filename)
                            with open(dest, 'wb') as f:
                                f.write(response.content)
                            conn.execute("INSERT INTO tracks (filename, display_name, uploaded_at) VALUES (?, ?, ?)",
                                         (filename, display_name, datetime.utcnow().isoformat()))
                            conn.execute("UPDATE add_track_requests SET approved = 1 WHERE id = ?", (req_id,))
                            conn.commit()
                            message = "Запрос на добавление трека одобрен и трек добавлен."
                        else:
                            message = "Ошибка скачивания файла по ссылке."
                    except Exception as e:
                        message = "Ошибка: " + str(e)
                else:
                    pass  # для файла - предполагаем, что файл уже загружен, но в этой реализации файлы загружаются пользователем отдельно
            conn.close()
        if request.form.get('reject_add_request'):
            req_id = int(request.form.get('reject_add_request'))
            conn = get_db()
            conn.execute("UPDATE add_track_requests SET rejected = 1 WHERE id = ?", (req_id,))
            conn.commit()
            conn.close()
            message = "Запрос на добавление трека отклонён."
        if request.form.get('delete_chat_msg'):
            msg_id = int(request.form.get('delete_chat_msg'))
            conn = get_db()
            conn.execute("UPDATE chat_messages SET deleted = 1 WHERE id = ?", (msg_id,))
            conn.commit()
            conn.close()
            message = "Сообщение в чате удалено."

    conn = get_db()
    tracks = conn.execute("SELECT * FROM tracks ORDER BY uploaded_at DESC").fetchall()
    users = conn.execute("SELECT username, banned, last_seen, created_at FROM users ORDER BY created_at DESC").fetchall()
    state = conn.execute("SELECT * FROM state WHERE id = 1").fetchone()
    online = []
    now = datetime.utcnow()
    for u in users:
        if u['last_seen']:
            try:
                t = datetime.fromisoformat(u['last_seen'])
                if now - t <= timedelta(minutes=5):
                    online.append(u['username'])
            except:
                pass
    reports = conn.execute("SELECT * FROM reports ORDER BY created_at DESC LIMIT 50").fetchall()
    likes_stats = conn.execute("""SELECT t.id, t.display_name, 
                                 (SELECT COUNT(*) FROM likes l WHERE l.track_id = t.id) AS likes
                                 FROM tracks t ORDER BY likes DESC""").fetchall()
    requests = conn.execute("SELECT r.id, r.username, r.track_id, t.display_name, r.requested_at, r.approved FROM requests r JOIN tracks t ON r.track_id = t.id ORDER BY r.requested_at DESC LIMIT 50").fetchall()
    add_requests = conn.execute("SELECT * FROM add_track_requests ORDER BY requested_at DESC LIMIT 50").fetchall()
    chat_msgs = conn.execute("SELECT * FROM chat_messages ORDER BY created_at DESC LIMIT 50").fetchall()
    conn.close()
    return render_template_string(ADMIN_TEMPLATE, tracks=tracks, message=message, state=state, users=users, online=online, reports=reports, likes_stats=likes_stats, requests=requests, add_requests=add_requests, chat_msgs=chat_msgs)

# ========== API: next ==========
@app.route('/api/next', methods=['POST'])
def api_next():
    conn = get_db()
    rows = conn.execute("SELECT id FROM tracks").fetchall()
    if not rows:
        conn.close()
        return jsonify({"ok": False, "error": "Нет треков"}), 400
    ids = [r['id'] for r in rows]
    state = conn.execute("SELECT * FROM state WHERE id = 1").fetchone()
    current = state['current_track_id']
    candidates = [i for i in ids if i != current]
    if not candidates:
        candidates = ids
    new_id = random.choice(candidates)
    greeting = set_current_track(new_id)
    row = conn.execute("SELECT filename, display_name FROM tracks WHERE id = ?", (new_id,)).fetchone()
    conn.close()
    filename = row['filename']
    display_name = row['display_name']
    ts = int(time.time() * 1000)
    return jsonify({"ok": True, "id": new_id, "filename": filename, "display_name": display_name, "greeting": greeting, "ts": ts})

# ========== API: like ==========
@app.route('/api/like', methods=['POST'])
@login_required
def api_like():
    data = request.json or {}
    tid = int(data.get('track_id') or 0)
    if tid <= 0:
        return jsonify({"ok": False, "error": "Invalid track_id"}), 400
    conn = get_db()
    row = conn.execute("SELECT * FROM likes WHERE track_id = ? AND username = ?", (tid, session['username'])).fetchone()
    if row:
        conn.close()
        return jsonify({"ok": False, "error": "Уже лайкнуто"}), 400
    conn.execute("INSERT INTO likes (track_id, username, liked_at) VALUES (?, ?, ?)",
                 (tid, session['username'], datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    log_action("info", f"{session['username']} liked track {tid}")
    return jsonify({"ok": True})

# ========== API: report ==========
@app.route('/api/report', methods=['POST'])
@login_required
def api_report():
    data = request.json or {}
    tid = int(data.get('track_id') or 0)
    reason = (data.get('reason') or '').strip()
    if not reason:
        return jsonify({"ok": False, "error": "Причина пуста"}), 400
    conn = get_db()
    conn.execute("INSERT INTO reports (username, reason, track_id, created_at) VALUES (?, ?, ?, ?)",
                 (session['username'], reason, tid, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    log_action("info", f"Report by {session['username']} for track {tid}: {reason}")
    return jsonify({"ok": True})

# ========== API: request ==========
@app.route('/api/request', methods=['POST'])
@login_required
def api_request():
    data = request.json or {}
    tid = int(data.get('track_id') or 0)
    if tid <= 0:
        return jsonify({"ok": False, "error": "Invalid track_id"}), 400
    conn = get_db()
    row = conn.execute("SELECT * FROM requests WHERE track_id = ? AND username = ? AND approved = 0", (tid, session['username'])).fetchone()
    if row:
        conn.close()
        return jsonify({"ok": False, "error": "Уже запрошено"}), 400
    conn.execute("INSERT INTO requests (username, track_id, requested_at) VALUES (?, ?, ?)",
                 (session['username'], tid, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    log_action("info", f"{session['username']} requested track {tid}")
    return jsonify({"ok": True})

# ========== API: add track request ==========
@app.route('/api/add_track_request', methods=['POST'])
@login_required
def api_add_track_request():
    data = request.json or {}
    url = data.get('url', '').strip()
    display_name = data.get('display_name', '').strip()
    if not url or not display_name:
        return jsonify({"ok": False, "error": "Укажите ссылку и название"}), 400
    conn = get_db()
    conn.execute("INSERT INTO add_track_requests (username, url_or_file, display_name, requested_at) VALUES (?, ?, ?, ?)",
                 (session['username'], url, display_name, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    log_action("info", f"{session['username']} requested to add track {display_name} from {url}")
    return jsonify({"ok": True})

# ========== API: chat ==========
@app.route('/api/chat', methods=['GET', 'POST'])
@login_required
def api_chat():
    if request.method == 'POST':
        message_text = (request.json.get('message') or '').strip()
        if not message_text:
            return jsonify({"ok": False, "error": "Сообщение пустое"}), 400
        if len(message_text) > 200:
            return jsonify({"ok": False, "error": "Слишком длинное сообщение"}), 400
        conn = get_db()
        conn.execute("INSERT INTO chat_messages (username, message, created_at) VALUES (?, ?, ?)",
                     (session['username'], message_text, datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    conn = get_db()
    rows = conn.execute("SELECT username, message, created_at FROM chat_messages WHERE deleted = 0 ORDER BY created_at DESC LIMIT 50").fetchall()
    conn.close()
    messages = [{"username": r['username'], "message": r['message'], "time": r['created_at']} for r in rows]
    return jsonify({"ok": True, "messages": messages})

# ========== API: heartbeat ==========
@app.route('/api/heartbeat', methods=['POST'])
@login_required
def api_heartbeat():
    try:
        conn = get_db()
        conn.execute("UPDATE users SET last_seen = ? WHERE username = ?", (datetime.utcnow().isoformat(), session['username']))
        conn.commit()
        now = datetime.utcnow()
        rows = conn.execute("SELECT COUNT(*) as c FROM users WHERE last_seen > ?", ((now - timedelta(minutes=5)).isoformat(),)).fetchone()
        online_count = rows['c']
        conn.close()
        return jsonify({"ok": True, "online": online_count})
    except:
        return jsonify({"ok": False}), 500

# ========== API: recent ==========
@app.route('/api/recent', methods=['GET'])
def api_recent():
    conn = get_db()
    rows = conn.execute("SELECT * FROM tracks ORDER BY uploaded_at DESC LIMIT 20").fetchall()
    res = []
    for r in rows:
        likes = conn.execute("SELECT COUNT(*) as c FROM likes WHERE track_id = ?", (r['id'],)).fetchone()['c']
        res.append({"track_id": r['id'], "display_name": r['display_name'], "uploaded_at": r['uploaded_at'], "likes": likes, "filename": r['filename']})
    conn.close()
    return jsonify({"ok": True, "recent": res})

# ========== Radio page ==========
@app.route('/radio')
@login_required
def radio_page():
    conn = get_db()
    rowu = conn.execute("SELECT banned FROM users WHERE username = ?", (session['username'],)).fetchone()
    if rowu and rowu['banned']:
        conn.close()
        session.clear()
        return "Ваш аккаунт заблокирован.", 403
    state = conn.execute("SELECT * FROM state WHERE id = 1").fetchone()
    if state['current_track_id'] is None:
        row = conn.execute("SELECT id FROM tracks ORDER BY RANDOM() LIMIT 1").fetchone()
        if row:
            set_current_track(row['id'])
            state = get_state()
    if state['current_track_id'] is None:
        conn.close()
        return render_template_string(RADIO_TEMPLATE_EMPTY)
    track_row = conn.execute("SELECT * FROM tracks WHERE id = ?", (state['current_track_id'],)).fetchone()
    conn.close()
    if not track_row:
        return render_template_string(RADIO_TEMPLATE_EMPTY)
    conn2 = get_db()
    likes_count = conn2.execute("SELECT COUNT(*) as c FROM likes WHERE track_id = ?", (track_row['id'],)).fetchone()['c']
    conn2.close()
    # generate greeting for initial
    voice_file = generate_greeting(state['current_display_name'] or track_row['display_name'])
    ts = int(time.time() * 1000)
    return render_template_string(RADIO_TEMPLATE,
                                  username=session.get('username'),
                                  is_admin=session.get('is_admin', False),
                                  track_id=track_row['id'],
                                  track_filename=track_row['filename'],
                                  track_display=state['current_display_name'] or track_row['display_name'],
                                  volume=state['volume'],
                                  likes_count=likes_count,
                                  voice_file=voice_file,
                                  ts=ts)

# ========== Templates ==========
REG_TEMPLATE_LOGIN = """
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Вход - Радио Фонтан</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  @keyframes fadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
  .fade-in { animation: fadeIn 0.5s ease-out; }
</style>
</head>
<body class="min-h-screen bg-gradient-to-br from-purple-800 via-indigo-700 to-blue-600 flex items-center justify-center">
  <div class="bg-white/90 backdrop-blur-md p-8 rounded-3xl shadow-xl w-96 transform transition-all hover:scale-105">
    <h1 class="text-3xl font-bold mb-6 text-center text-indigo-800 fade-in">Вход — Радио Фонтан</h1>
    {% if error %}<p class="text-red-600 text-center mb-4 fade-in">{{ error }}</p>{% endif %}
    <form method="POST" class="flex flex-col gap-4">
      <input name="username" placeholder="Ник" required class="p-3 border border-indigo-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 transition" />
      <input name="password" type="password" placeholder="Пароль" required class="p-3 border border-indigo-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 transition" />
      <button class="bg-indigo-600 text-white p-3 rounded-lg mt-2 hover:bg-indigo-700 transition fade-in">Войти</button>
    </form>
    <p class="text-sm mt-4 text-center fade-in">Нет аккаунта? <a href="/register" class="text-indigo-600 hover:underline">Регистрация</a></p>
  </div>
</body>
</html>
"""

REG_TEMPLATE_REGISTER = """
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Регистрация - Радио Фонтан</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  @keyframes fadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
  .fade-in { animation: fadeIn 0.5s ease-out; }
</style>
</head>
<body class="min-h-screen bg-gradient-to-br from-purple-800 via-indigo-700 to-blue-600 flex items-center justify-center">
  <div class="bg-white/90 backdrop-blur-md p-8 rounded-3xl shadow-xl w-96 transform transition-all hover:scale-105">
    <h1 class="text-3xl font-bold mb-6 text-center text-indigo-800 fade-in">Регистрация</h1>
    {% if error %}<p class="text-red-600 text-center mb-4 fade-in">{{ error }}</p>{% endif %}
    <form method="POST" class="flex flex-col gap-4">
      <input name="username" placeholder="Ник (не менее 4 символов, буквы/цифры/_)" required class="p-3 border border-indigo-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 transition" />
      <input name="password" type="password" placeholder="Пароль (минимум 8, буквы+цифры)" required class="p-3 border border-indigo-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 transition" />
      <label class="flex items-center gap-2 text-sm text-gray-700 fade-in">
        <input type="checkbox" name="agreed_to_terms" class="rounded" />
        Согласен с <a href="/terms" class="text-indigo-600 hover:underline">Пользовательским соглашением</a>
      </label>
      <label class="flex items-center gap-2 text-sm text-gray-700 fade-in">
        <input type="checkbox" name="agreed_to_privacy" class="rounded" />
        Согласен с <a href="/privacy" class="text-indigo-600 hover:underline">Политикой конфиденциальности</a>
      </label>
      <label class="flex items-center gap-2 text-sm text-gray-700 fade-in">
        <input type="checkbox" name="agreed_to_data" class="rounded" />
        Даю <a href="/data_consent" class="text-indigo-600 hover:underline">согласие на обработку персональных данных</a>
      </label>
      <button class="bg-indigo-600 text-white p-3 rounded-lg mt-2 hover:bg-indigo-700 transition fade-in">Создать</button>
    </form>
    <p class="text-sm mt-4 text-center fade-in">Уже есть аккаунт? <a href="/" class="text-indigo-600 hover:underline">Войти</a></p>
  </div>
</body>
</html>
"""

@app.route('/terms')
def terms():
    return render_template_string("""
<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>Пользовательское соглашение - Радио Фонтан</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="min-h-screen bg-gradient-to-r from-purple-700 to-indigo-700 p-8 text-white">
  <div class="max-w-4xl mx-auto bg-white/10 backdrop-blur-md p-6 rounded-xl">
    <h1 class="text-3xl font-bold mb-4">Пользовательское соглашение</h1>
    <p>1. Общие положения

1.1. Настоящее Соглашение является публичной офертой.
1.2. Использование сайта возможно только на условиях данного Соглашения.
1.3. Если Пользователь не согласен с условиями Соглашения, он обязан прекратить использование сайта.

2. Термины и определения

Сайт — веб-ресурс, расположенный по адресу [https://fontan.onrender.com/].

Пользователь — физическое лицо, использующее функционал сайта.

Администрация — владельцы и/или уполномоченные представители сайта.

Контент — любые материалы, размещённые на сайте: тексты, изображения, аудио, видео и т.д.

3. Права и обязанности Пользователя

3.1. Пользователь обязуется:

Не нарушать действующее законодательство.

Не размещать материалы, содержащие нецензурную лексику, оскорбления, насилие, экстремизм, порнографию, а также материалы, нарушающие авторские права.

Не предпринимать действий, направленных на несанкционированный доступ к данным других пользователей или к серверу сайта.

3.2. Пользователь имеет право:

Использовать сайт в пределах его функционала.

Загружать и публиковать свой контент (если такая функция доступна).

Получать информацию и пользоваться услугами, предоставляемыми сайтом.

4. Права и обязанности Администрации

4.1. Администрация имеет право:

Изменять функционал сайта без предварительного уведомления.

Удалять любой контент, нарушающий условия Соглашения.

Блокировать аккаунты пользователей при нарушении правил.

Вносить изменения в данное Соглашение.

4.2. Администрация обязуется:

Поддерживать работоспособность сайта, кроме случаев технических работ или форс-мажора.

Не передавать личные данные пользователей третьим лицам без их согласия, за исключением случаев, предусмотренных законом.

5. Интеллектуальная собственность

5.1. Все права на материалы, размещённые на сайте, принадлежат их законным владельцам.
5.2. Пользователь, размещая контент, предоставляет Администрации неисключительное право использовать его для обеспечения работы сайта.
5.3. Копирование, распространение и использование материалов сайта без разрешения запрещено.

6. Ответственность сторон

6.1. Пользователь несёт ответственность за достоверность предоставляемых данных и публикуемого контента.
6.2. Администрация не несёт ответственности за:

Перебои в работе сайта;

Утрату данных пользователя;

Контент, размещённый пользователями.

7. Обработка персональных данных

7.1. Регистрируясь на сайте, Пользователь даёт согласие на обработку своих персональных данных в соответствии с действующим законодательством.
7.2. Персональные данные используются исключительно для обеспечения работы сайта и коммуникации с Пользователем.

8. Заключительные положения

8.1. Настоящее Соглашение может быть изменено Администрацией в любое время.
8.2. Новая редакция Соглашения вступает в силу с момента её публикации на сайте.
8.3. Продолжение использования сайта означает согласие с изменённой редакцией.
8.4. Все споры и разногласия решаются путём переговоров, а при невозможности — в судебном порядке по месту регистрации Администрации сайта.

📜 Дата последнего обновления: [31.10.2025]</p>
    <a href="/register" class="text-blue-300 mt-4 block">Назад</a>
  </div>
</body>
</html>
    """)

@app.route('/privacy')
def privacy():
    return render_template_string("""
<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>Политика конфиденциальности - Радио Фонтан</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="min-h-screen bg-gradient-to-r from-purple-700 to-indigo-700 p-8 text-white">
  <div class="max-w-4xl mx-auto bg-white/10 backdrop-blur-md p-6 rounded-xl">
    <h1 class="text-3xl font-bold mb-4">Политика конфиденциальности</h1>
    <p>З1. Общие положения

1.1. Настоящая Политика разработана в соответствии с законодательством о защите персональных данных.
1.2. Цель Политики — обеспечить защиту персональных данных, которые Пользователь предоставляет при использовании Сайта.
1.3. Использование Сайта означает согласие Пользователя с данной Политикой. В случае несогласия с условиями, Пользователь должен прекратить использование Сайта.

2. Состав собираемых данных

2.1. Администрация может собирать следующие данные:

Имя пользователя, логин, адрес электронной почты, пароль;

Загруженные аудио-файлы, голосовые приветствия и иные материалы;

IP-адрес, данные о браузере и операционной системе;

Данные о действиях на Сайте (просмотры страниц, загрузки, клики и т.д.);

Информацию, предоставленную добровольно (например, при обратной связи).

3. Цели обработки данных

3.1. Персональные данные собираются и используются для:

Регистрации и авторизации пользователей;

Обеспечения работы функций Сайта (загрузка треков, прослушивание, участие в радио-эфире и т.д.);

Связи с пользователем по вопросам, связанным с работой Сайта;

Улучшения качества обслуживания и удобства использования;

Обеспечения безопасности и предотвращения мошенничества.

4. Хранение и защита персональных данных

4.1. Персональные данные хранятся в защищённых базах данных, доступ к которым имеет ограниченное число лиц.
4.2. Администрация принимает все необходимые технические и организационные меры для защиты данных от несанкционированного доступа, изменения, утраты или распространения.
4.3. Пароли пользователей хранятся в зашифрованном виде.

5. Передача данных третьим лицам

5.1. Администрация не передаёт персональные данные третьим лицам, кроме случаев:

Если это требуется по закону (по запросу государственных органов);

Если Пользователь дал явное согласие;

Если передача необходима для функционирования Сайта (например, хостинг-провайдерам или сервисам рассылок), при условии соблюдения ими конфиденциальности.

6. Использование файлов cookie

6.1. Сайт может использовать файлы cookie для:

Сохранения пользовательских настроек;

Анализа поведения посетителей;

Повышения удобства использования сайта.
6.2. Пользователь может отключить использование cookie в настройках своего браузера, но это может ограничить функционал Сайта.

7. Права пользователя

Пользователь имеет право:

Запрашивать информацию о своих персональных данных, обрабатываемых Администрацией;

Требовать исправления, блокировки или удаления своих данных;

Отозвать согласие на обработку персональных данных, связавшись с Администрацией по адресу: [указать e-mail].

8. Ответственность

8.1. Администрация не несёт ответственности за действия третьих лиц, получивших доступ к данным в результате неправомерных действий Пользователя (например, разглашения пароля).
8.2. Администрация не несёт ответственности за контент, размещённый пользователями.

9. Изменения в Политике

9.1. Администрация имеет право изменять настоящую Политику в любое время без предварительного уведомления.
9.2. Новая версия вступает в силу с момента её публикации на Сайте.
9.3. Продолжение использования Сайта означает согласие Пользователя с обновлённой редакцией Политики.

10. Контактная информация

По всем вопросам, связанным с обработкой персональных данных, можно обратиться по адресу:
📧 [fontanradiohelp@gmail.com]

📜 Дата последнего обновления: [31.10.2025]</p>
    <a href="/register" class="text-blue-300 mt-4 block">Назад</a>
  </div>
</body>
</html>
    """)

@app.route('/data_consent')
def data_consent():
    return render_template_string("""
<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>Согласие на обработку данных - Радио Фонтан</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="min-h-screen bg-gradient-to-r from-purple-700 to-indigo-700 p-8 text-white">
  <div class="max-w-4xl mx-auto bg-white/10 backdrop-blur-md p-6 rounded-xl">
    <h1 class="text-3xl font-bold mb-4">Согласие на обработку персональных данных</h1>
    <p>Персональные данные, на обработку которых даётся согласие

Пользователь предоставляет Администрации Сайта следующие данные:

имя или псевдоним;

адрес электронной почты;

данные, предоставленные при регистрации на Сайте;

IP-адрес, сведения о браузере и устройстве;

иные данные, передаваемые при использовании Сайта (включая загруженные аудио-файлы и сообщения).

2. Цели обработки персональных данных

Персональные данные обрабатываются в целях:

регистрации Пользователя на Сайте;

обеспечения функционирования личного кабинета и сервисов;

предоставления доступа к функциям (загрузка треков, голосовые приветствия и т. д.);

направления уведомлений, ответов на запросы и сообщений;

улучшения качества работы Сайта и пользовательского опыта;

соблюдения требований законодательства.

3. Условия обработки и хранения данных

3.1. Обработка персональных данных осуществляется с использованием автоматизированных и неавтоматизированных средств.
3.2. Данные хранятся до достижения целей обработки либо до момента отзыва Пользователем согласия.
3.3. Передача персональных данных третьим лицам возможна только в случаях, предусмотренных законом или с согласия Пользователя.

4. Права пользователя

Пользователь имеет право:

получать информацию о своих персональных данных и порядке их обработки;

требовать уточнения, блокировки или удаления данных;

отзывать согласие на обработку персональных данных, направив уведомление на адрес электронной почты Администрации: [fontanradiohelp@gmail.com]
].

5. Отзыв согласия

Пользователь может отозвать своё согласие в любое время, направив письменное уведомление на электронную почту Администрации.
После получения уведомления обработка персональных данных прекращается, а данные удаляются в течение 30 календарных дней (если иное не предусмотрено законом).

6. Заключительные положения

6.1. Настоящее согласие действует бессрочно с момента предоставления данных.
6.2. Продолжая использование Сайта, Пользователь подтверждает, что ознакомлен с условиями обработки персональных данных и даёт согласие на их обработку.

📜 Дата последнего обновления: [31.10.2025]</p>
    <a href="/register" class="text-blue-300 mt-4 block">Назад</a>
  </div>
</body>
</html>
    """)

ADMIN_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Админ — Радио Фонтан</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
  .fade-in { animation: fadeIn 0.3s ease-in; }
  table tr:hover { background: #f3f4f6; transition: background 0.2s; }
</style>
</head>
<body class="min-h-screen bg-gradient-to-br from-indigo-800 to-purple-800 p-8">
  <div class="max-w-7xl mx-auto bg-white/90 backdrop-blur-md p-8 rounded-3xl shadow-2xl">
    <div class="flex justify-between items-center mb-6 fade-in">
      <h1 class="text-3xl font-bold text-indigo-800">Админская панель — Радио Фонтан</h1>
      <div class="flex gap-4">
        <a href="/radio" class="text-indigo-600 hover:underline">На радио</a>
        <a href="/logout" class="text-red-600 hover:underline">Выйти</a>
      </div>
    </div>
    {% if message %}<p class="text-green-600 mb-4 text-center fade-in">{{ message }}</p>{% endif %}

    <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
      <div class="p-6 bg-white rounded-xl shadow-md fade-in">
        <h2 class="font-semibold mb-3 text-lg">Загрузить трек (MP3)</h2>
        <form method="POST" enctype="multipart/form-data" class="flex flex-col gap-3">
          <input type="file" name="track" accept=".mp3" class="border p-2 rounded-lg" />
          <input type="text" name="display_name" placeholder="Название (опционально)" class="p-2 border rounded-lg" />
          <button class="bg-indigo-600 text-white p-2 rounded-lg hover:bg-indigo-700 transition">Загрузить</button>
        </form>
      </div>

      <div class="p-6 bg-white rounded-xl shadow-md fade-in">
        <h2 class="font-semibold mb-3 text-lg">Громкость по умолчанию</h2>
        <form method="POST" class="flex items-center gap-3">
          <input type="number" name="set_volume" min="0" max="1" step="0.05" value="{{ state['volume'] }}" class="p-2 border rounded-lg w-24" />
          <button class="bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 transition">Сохранить</button>
        </form>
      </div>
    </div>

    <section class="mb-6 fade-in">
      <h2 class="font-semibold mb-3 text-lg">Кто слушает сейчас ({{ online|length }})</h2>
      {% if online %}
        <ul class="grid grid-cols-2 md:grid-cols-4 gap-2">
        {% for u in online %}
          <li class="bg-green-100 text-green-800 p-2 rounded-lg">{{ u }}</li>
        {% endfor %}
        </ul>
      {% else %}
        <p class="text-gray-600">Никто не слушает сейчас.</p>
      {% endif %}
    </section>

    <section class="mb-6 fade-in">
      <h2 class="font-semibold mb-3 text-lg">Пользователи</h2>
      <div class="overflow-x-auto">
        <table class="w-full text-left border-collapse">
          <thead class="bg-indigo-100"><tr><th class="p-3">Ник</th><th class="p-3">Бан</th><th class="p-3">Last seen</th><th class="p-3">Действия</th></tr></thead>
          <tbody>
          {% for u in users %}
            <tr class="border-t">
              <td class="p-3">{{ u['username'] }}</td>
              <td class="p-3">{{ u['banned'] }}</td>
              <td class="p-3">{{ u['last_seen'] or '-' }}</td>
              <td class="p-3">
                <form method="POST" style="display:inline">
                  {% if u['banned'] %}
                    <input type="hidden" name="unban_user" value="{{ u['username'] }}" />
                    <button class="px-3 py-1 rounded bg-green-500 text-white hover:bg-green-600 transition">Разбан</button>
                  {% else %}
                    <input type="hidden" name="ban_user" value="{{ u['username'] }}" />
                    <button class="px-3 py-1 rounded bg-red-500 text-white hover:bg-red-600 transition">Забанить</button>
                  {% endif %}
                </form>
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </section>

    <section class="mb-6 fade-in">
      <h2 class="font-semibold mb-3 text-lg">Статистика лайков</h2>
      <ul class="grid grid-cols-1 md:grid-cols-2 gap-2">
      {% for s in likes_stats %}
        <li class="bg-blue-100 p-2 rounded-lg">{{ s['display_name'] }} — {{ s['likes'] }} ❤</li>
      {% endfor %}
      </ul>
    </section>

    <section class="mb-6 fade-in">
      <h2 class="font-semibold mb-3 text-lg">Жалобы</h2>
      <div class="overflow-x-auto">
        <table class="w-full text-left border-collapse">
          <thead class="bg-yellow-100"><tr><th class="p-3">ID</th><th class="p-3">Пользователь</th><th class="p-3">Причина</th><th class="p-3">Трек</th><th class="p-3">Когда</th><th class="p-3">Действие</th></tr></thead>
          <tbody>
          {% for r in reports %}
            <tr class="border-t">
              <td class="p-3">{{ r['id'] }}</td>
              <td class="p-3">{{ r['username'] }}</td>
              <td class="p-3">{{ r['reason'] }}</td>
              <td class="p-3">{{ r['track_id'] }}</td>
              <td class="p-3">{{ r['created_at'] }}</td>
              <td class="p-3">
                {% if r['processed'] == 0 %}
                <form method="POST" style="display:inline">
                  <input type="hidden" name="process_report" value="{{ r['id'] }}" />
                  <button class="px-3 py-1 rounded bg-indigo-600 text-white hover:bg-indigo-700 transition">Обработать</button>
                </form>
                {% else %}
                  <span class="text-green-600">Обработана</span>
                {% endif %}
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </section>

    <section class="mb-6 fade-in">
      <h2 class="font-semibold mb-3 text-lg">Запросы треков</h2>
      <div class="overflow-x-auto">
        <table class="w-full text-left border-collapse">
          <thead class="bg-green-100"><tr><th class="p-3">ID</th><th class="p-3">Пользователь</th><th class="p-3">Трек</th><th class="p-3">Когда</th><th class="p-3">Действие</th></tr></thead>
          <tbody>
          {% for req in requests %}
            <tr class="border-t">
              <td class="p-3">{{ req['id'] }}</td>
              <td class="p-3">{{ req['username'] }}</td>
              <td class="p-3">{{ req['display_name'] }} (ID: {{ req['track_id'] }})</td>
              <td class="p-3">{{ req['requested_at'] }}</td>
              <td class="p-3">
                {% if req['approved'] == 0 %}
                <form method="POST" style="display:inline">
                  <input type="hidden" name="approve_request" value="{{ req['id'] }}" />
                  <button class="px-3 py-1 rounded bg-green-500 text-white hover:bg-green-600 transition">Одобрить</button>
                </form>
                {% else %}
                  <span class="text-green-600">Одобрено</span>
                {% endif %}
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </section>

    <section class="mb-6 fade-in">
      <h2 class="font-semibold mb-3 text-lg">Запросы на добавление треков</h2>
      <div class="overflow-x-auto">
        <table class="w-full text-left border-collapse">
          <thead class="bg-purple-100"><tr><th class="p-3">ID</th><th class="p-3">Пользователь</th><th class="p-3">Ссылка/Файл</th><th class="p-3">Название</th><th class="p-3">Когда</th><th class="p-3">Действия</th></tr></thead>
          <tbody>
          {% for req in add_requests %}
            <tr class="border-t">
              <td class="p-3">{{ req['id'] }}</td>
              <td class="p-3">{{ req['username'] }}</td>
              <td class="p-3">{{ req['url_or_file'] }}</td>
              <td class="p-3">{{ req['display_name'] }}</td>
              <td class="p-3">{{ req['requested_at'] }}</td>
              <td class="p-3 flex gap-2">
                {% if req['approved'] == 0 and req['rejected'] == 0 %}
                <form method="POST" style="display:inline">
                  <input type="hidden" name="approve_add_request" value="{{ req['id'] }}" />
                  <button class="px-3 py-1 rounded bg-green-500 text-white hover:bg-green-600 transition">Одобрить</button>
                </form>
                <form method="POST" style="display:inline">
                  <input type="hidden" name="reject_add_request" value="{{ req['id'] }}" />
                  <button class="px-3 py-1 rounded bg-red-500 text-white hover:bg-red-600 transition">Отклонить</button>
                </form>
                {% elif req['approved'] %}
                  <span class="text-green-600">Одобрено</span>
                {% elif req['rejected'] %}
                  <span class="text-red-600">Отклонено</span>
                {% endif %}
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </section>

    <section class="mb-6 fade-in">
      <h2 class="font-semibold mb-3 text-lg">Сообщения в чате (модерация)</h2>
      <div class="overflow-x-auto">
        <table class="w-full text-left border-collapse">
          <thead class="bg-red-100"><tr><th class="p-3">ID</th><th class="p-3">Пользователь</th><th class="p-3">Сообщение</th><th class="p-3">Когда</th><th class="p-3">Действие</th></tr></thead>
          <tbody>
          {% for msg in chat_msgs %}
            <tr class="border-t">
              <td class="p-3">{{ msg['id'] }}</td>
              <td class="p-3">{{ msg['username'] }}</td>
              <td class="p-3">{{ msg['message'] }}</td>
              <td class="p-3">{{ msg['created_at'] }}</td>
              <td class="p-3">
                {% if msg['deleted'] == 0 %}
                <form method="POST" style="display:inline" onsubmit="return confirm('Удалить сообщение?')">
                  <input type="hidden" name="delete_chat_msg" value="{{ msg['id'] }}" />
                  <button class="px-3 py-1 rounded bg-red-500 text-white hover:bg-red-600 transition">Удалить</button>
                </form>
                {% else %}
                  <span class="text-red-600">Удалено</span>
                {% endif %}
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </section>

    <section class="fade-in">
      <h2 class="font-semibold mb-3 text-lg">Список треков</h2>
      <div class="overflow-x-auto">
        <table class="w-full text-left border-collapse">
          <thead class="bg-gray-100"><tr><th class="p-3">ID</th><th class="p-3">Название</th><th class="p-3">Файл</th><th class="p-3">Действия</th></tr></thead>
          <tbody>
          {% for t in tracks %}
            <tr class="border-t">
              <td class="p-3">{{ t['id'] }}</td>
              <td class="p-3">{{ t['display_name'] }}</td>
              <td class="p-3">{{ t['filename'] }}</td>
              <td class="p-3 flex gap-2">
                <form method="POST" style="display:inline" class="flex gap-2">
                  <input type="hidden" name="set_current" value="{{ t['id'] }}" />
                  <input type="text" name="current_display" placeholder="Имя для эфира" class="p-2 border rounded-lg text-sm" />
                  <button class="bg-green-500 text-white px-3 py-2 rounded-lg hover:bg-green-600 transition">Установить</button>
                </form>
                <form method="POST" style="display:inline" onsubmit="return confirm('Удалить?')">
                  <input type="hidden" name="delete_id" value="{{ t['id'] }}" />
                  <button class="bg-red-500 text-white px-3 py-2 rounded-lg hover:bg-red-600 transition">Удалить</button>
                </form>
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </section>

  </div>
</body>
</html>
"""

RADIO_TEMPLATE_EMPTY = """
<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><script src="https://cdn.tailwindcss.com"></script></head>
<body class="min-h-screen bg-gradient-to-br from-purple-800 to-indigo-700 flex items-center justify-center">
  <div class="bg-white/90 backdrop-blur-md p-8 rounded-3xl shadow-xl text-center w-96">
    <h1 class="text-3xl font-bold mb-4 text-indigo-800">Радио Фонтан</h1>
    <p class="text-gray-700">Пока нет загруженных треков.</p>
    <p class="mt-4"><a href="/logout" class="text-red-600 hover:underline">Выйти</a></p>
  </div>
</body>
</html>
"""

RADIO_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Радио Фонтан</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    .bar { width:8px; height:20px; margin:0 3px; display:inline-block; background:linear-gradient(180deg,#34d399,#06b6d4); border-radius:3px; transform-origin: bottom; transition: height 0.1s ease-out, opacity 0.1s; }
    .btn { padding:10px 16px; border-radius:12px; font-weight:600; transition: transform 0.2s, background 0.2s; }
    .btn:hover { transform: scale(1.05); }
    @keyframes pulse { 0% { opacity: 0.5; } 50% { opacity: 1; } 100% { opacity: 0.5; } }
    .pulse { animation: pulse 1.5s infinite; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
    .fade-in { animation: fadeIn 0.3s ease-out; }
    .chat-message { transition: background 0.2s; }
    .chat-message:hover { background: #f3f4f6; }
    .dark-mode { background: linear-gradient(135deg, #1e293b, #334155); color: white; }
    .dark-mode .bg-white/90 { background: #1f2937; color: white; }
    .dark-mode .text-gray-600 { color: #d1d5db; }
    .dark-mode .bg-indigo-600 { background: #4f46e5; }
    .dark-mode .bg-gray-100 { background: #374151; }
    .dark-mode .text-indigo-800 { color: #a5b4fc; }
    .dark-mode .text-gray-700 { color: #9ca3af; }
  </style>
</head>
<body class="min-h-screen bg-gradient-to-br from-indigo-800 to-purple-800 flex items-center justify-center p-4">
  <div class="bg-white/90 backdrop-blur-md p-8 rounded-3xl shadow-2xl w-full max-w-5xl fade-in">
    <div class="flex flex-col md:flex-row justify-between items-start md:items-center mb-6">
      <div>
        <h1 class="text-3xl font-bold text-indigo-800">Радио Фонтан</h1>
        <p class="text-sm text-gray-600">Привет, {{ username }} {% if is_admin %}(админ){% endif %}</p>
      </div>
      <div class="text-left md:text-right mt-4 md:mt-0">
        <p class="text-sm text-gray-600">Сейчас играет:</p>
        <p id="currName" class="font-semibold text-lg">{{ track_display }}</p>
        <p id="timeNow" class="text-xs text-gray-500"></p>
      </div>
    </div>

    <div class="mb-6 flex flex-wrap gap-4 items-center">
      <button id="startBtn" class="btn bg-indigo-600 text-white hover:bg-indigo-700">Запустить радио</button>
      <button id="nextBtn" class="btn bg-gray-200 hover:bg-gray-300">Следующий</button>
      <label class="text-sm text-gray-600">Громкость</label>
      <input id="volumeSlider" type="range" min="0" max="1" step="0.01" value="{{ volume }}" class="w-32 accent-indigo-600" />
      <button id="likeBtn" class="btn bg-rose-500 text-white hover:bg-rose-600">❤ Лайк (<span id="likesCount">{{ likes_count }}</span>)</button>
      <button id="reportBtn" class="btn bg-yellow-400 hover:bg-yellow-500">Пожаловаться</button>
      <button id="requestBtn" class="btn bg-green-500 text-white hover:bg-green-600">Запросить трек</button>
      <button id="addTrackBtn" class="btn bg-purple-500 text-white hover:bg-purple-600">Добавить трек</button>
      <button id="themeBtn" class="btn bg-gray-800 text-white hover:bg-gray-900">Тема</button>
      <span id="onlineCount" class="text-sm text-gray-600 ml-auto">Online: 0</span>
    </div>

    <div class="mb-6 flex justify-center">
      <div id="visual" class="flex items-end h-32 bg-gray-100 rounded-xl p-4 w-full max-w-lg"></div>
    </div>

    <div class="mb-4">
      <p id="status" class="text-gray-600 text-center">Статус: готов</p>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div class="fade-in">
        <h3 class="font-semibold mb-3 text-lg">Последние треки</h3>
        <ul id="recentList" class="list-disc pl-6 text-sm text-gray-700 max-h-48 overflow-y-auto"></ul>
      </div>

      <div class="fade-in">
        <h3 class="font-semibold mb-3 text-lg">Чат</h3>
        <div id="chatBox" class="bg-gray-100 p-4 rounded-xl max-h-48 overflow-y-auto mb-3"></div>
        <div class="flex gap-2">
          <input id="chatInput" placeholder="Сообщение..." class="flex-1 p-2 border rounded-lg" />
          <button id="chatSend" class="btn bg-indigo-600 text-white hover:bg-indigo-700">Отправить</button>
        </div>
      </div>
    </div>

    <div class="flex justify-between items-center mt-6">
      <div>
        <a href="/logout" class="text-red-600 hover:underline">Выйти</a>
        {% if is_admin %}<a href="/admin" class="ml-4 text-indigo-600 hover:underline">Админ</a>{% endif %}
      </div>
      <div id="likesInfo" class="text-sm text-gray-600"></div>
    </div>

    <!-- Аудио -->
    <audio id="music" crossorigin="anonymous" src="/music/{{ track_filename }}?t={{ ts }}"></audio>
    <audio id="greeting" src="/voice/{{ voice_file }}?t={{ ts }}"></audio>

  </div>

<script>
  // Элементы
  const startBtn = document.getElementById('startBtn');
  const music = document.getElementById('music');
  const greeting = document.getElementById('greeting');
  const status = document.getElementById('status');
  const visual = document.getElementById('visual');
  const volumeSlider = document.getElementById('volumeSlider');
  const nextBtn = document.getElementById('nextBtn');
  const likeBtn = document.getElementById('likeBtn');
  const reportBtn = document.getElementById('reportBtn');
  const requestBtn = document.getElementById('requestBtn');
  const addTrackBtn = document.getElementById('addTrackBtn');
  const themeBtn = document.getElementById('themeBtn');
  const recentList = document.getElementById('recentList');
  const currName = document.getElementById('currName');
  const likesCountSpan = document.getElementById('likesCount');
  const onlineCount = document.getElementById('onlineCount');
  const timeNow = document.getElementById('timeNow');
  const chatBox = document.getElementById('chatBox');
  const chatInput = document.getElementById('chatInput');
  const chatSend = document.getElementById('chatSend');

  // loading spinner
  const loadingSpinner = document.createElement('div');
  loadingSpinner.className = 'inline-block animate-spin rounded-full h-5 w-5 border-b-2 border-indigo-600 ml-2';
  loadingSpinner.style.display = 'none';
  status.appendChild(loadingSpinner);

  // Бары
  const BAR_COUNT = 30;
  for (let i = 0; i < BAR_COUNT; i++) {
    const b = document.createElement('div');
    b.className = 'bar';
    b.style.height = (5 + Math.random() * 20) + 'px';
    visual.appendChild(b);
  }
  const bars = Array.from(document.querySelectorAll('.bar'));

  // Audio
  let audioCtx, analyser, sourceNode;
  function setupAudioContext() {
    if (audioCtx) return;
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    try {
      sourceNode = audioCtx.createMediaElementSource(music);
      sourceNode.connect(analyser);
      analyser.connect(audioCtx.destination);
    } catch (e) {
      console.warn("MediaElementSource error", e);
    }
  }

  function animate() {
    if (!analyser) { requestAnimationFrame(animate); return; }
    const data = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(data);
    for (let i = 0; i < BAR_COUNT; i++) {
      const v = data[i * 2] || 0;
      const h = Math.max(4, (v / 255) * 140);
      bars[i].style.height = h + 'px';
      bars[i].style.opacity = (0.4 + v / 255 * 0.6);
      bars[i].style.transform = `scaleY(${0.6 + v / 255 * 1.4})`;
    }
    requestAnimationFrame(animate);
  }

  let greetingPlaying = false;
  let loadingNext = false;
  let currentTrackId = {{ track_id }};

  music.onended = async () => {
    status.innerText = "💤 Трек закончился — запрашиваем следующий...";
    await fetchNext();
  };

  greeting.onended = () => {
    greetingPlaying = false;
    const target = parseFloat(volumeSlider.value);
    status.innerText = "▶ Сейчас играет: " + currName.innerText;
    let t = 0;
    const steps = 30;
    const step = (target - music.volume) / steps;
    const intr = setInterval(() => {
      t++;
      music.volume = Math.max(0, Math.min(1, music.volume + step));
      if (t >= steps) { clearInterval(intr); music.volume = target; }
    }, 50);
  };

  async function fetchNext() {
    if (loadingNext) return;
    loadingNext = true;
    nextBtn.disabled = true;
    status.classList.add('pulse');
    loadingSpinner.style.display = 'inline-block';
    try {
      const res = await fetch('/api/next', { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        greeting.pause(); greeting.currentTime = 0;
        music.pause(); music.currentTime = 0;
        greeting.src = '/voice/' + data.greeting + '?t=' + data.ts;
        music.src = '/music/' + data.filename + '?t=' + data.ts;
        currName.innerText = data.display_name;
        currentTrackId = data.id;
        status.innerText = "▶ Загружаем новый трек: " + data.display_name;
        music.load(); greeting.load();
        music.volume = 0.02;
        await music.play();
        await greeting.play();
        greetingPlaying = true;
        loadRecent();
        updateLikes();
      } else {
        status.innerText = "Ошибка сервера при получении следующего трека";
      }
    } catch (e) {
      status.innerText = "Сетевая ошибка при получении следующего трека";
    } finally {
      loadingNext = false;
      nextBtn.disabled = false;
      status.classList.remove('pulse');
      loadingSpinner.style.display = 'none';
    }
  }

  nextBtn.addEventListener('click', async () => {
    status.innerText = "Запрос следующего...";
    await fetchNext();
  });

  startBtn.addEventListener('click', async () => {
    setupAudioContext();
    if (audioCtx && audioCtx.state === 'suspended') await audioCtx.resume();
    const ts = Date.now();
    music.src = music.src.split('?')[0] + '?t=' + ts;
    greeting.src = greeting.src.split('?')[0] + '?t=' + ts;
    music.volume = 0.02;
    greeting.volume = 1.0;
    status.innerText = "▶ Запуск: приветствие + тихая музыка";
    await music.play();
    await greeting.play();
    greetingPlaying = true;
    animate();
  });

  volumeSlider.addEventListener('input', () => {
    const v = parseFloat(volumeSlider.value);
    if (!greetingPlaying) music.volume = v;
  });

  likeBtn.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/like', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ track_id: currentTrackId }) });
      const data = await res.json();
      if (data.ok) {
        status.innerText = "Спасибо за лайк!";
        updateLikes();
      } else {
        status.innerText = data.error || "Не получилось поставить лайк";
      }
    } catch (e) {
      status.innerText = "Ошибка сети при лайке";
    }
  });

  async function updateLikes() {
    try {
      const res = await fetch('/api/recent');
      const data = await res.json();
      if (data.ok) {
        const cur = data.recent.find(r => r.track_id === currentTrackId);
        if (cur) likesCountSpan.innerText = cur.likes;
      }
    } catch (e) { }
  }

  reportBtn.addEventListener('click', async () => {
    const reason = prompt("Опишите причину жалобы (коротко):");
    if (!reason) return;
    try {
      const res = await fetch('/api/report', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ track_id: currentTrackId, reason }) });
      const data = await res.json();
      if (data.ok) {
        alert("Жалоба отправлена. Спасибо.");
      } else {
        alert("Ошибка: " + (data.error || 'unknown'));
      }
    } catch (e) {
      alert("Ошибка сети");
    }
  });

  requestBtn.addEventListener('click', async () => {
    if (confirm("Запросить текущий трек в эфир?")) {
      try {
        const res = await fetch('/api/request', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ track_id: currentTrackId }) });
        const data = await res.json();
        if (data.ok) {
          alert("Запрос отправлен администратору.");
        } else {
          alert("Ошибка: " + (data.error || 'unknown'));
        }
      } catch (e) {
        alert("Ошибка сети");
      }
    }
  });

  addTrackBtn.addEventListener('click', async () => {
    const url = prompt("Ссылка на MP3:");
    const name = prompt("Название трека:");
    if (!url || !name) return;
    try {
      const res = await fetch('/api/add_track_request', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url, display_name: name }) });
      const data = await res.json();
      if (data.ok) {
        alert("Запрос отправлен администратору.");
      } else {
        alert("Ошибка: " + (data.error || 'unknown'));
      }
    } catch (e) {
      alert("Ошибка сети");
    }
  });

  async function loadRecent() {
    try {
      const res = await fetch('/api/recent');
      const data = await res.json();
      if (data.ok) {
        recentList.innerHTML = '';
        data.recent.forEach(r => {
          const li = document.createElement('li');
          li.innerText = `${r.display_name} (${r.likes}❤) - ${r.uploaded_at}`;
          li.classList.add('fade-in');
          recentList.appendChild(li);
        });
      }
    } catch (e) { }
  }

  async function loadChat() {
    try {
      const res = await fetch('/api/chat');
      const data = await res.json();
      if (data.ok) {
        chatBox.innerHTML = '';
        data.messages.reverse().forEach(m => {
          const div = document.createElement('div');
          div.className = 'chat-message p-2 rounded-lg mb-2';
          div.innerHTML = `<strong>${m.username}:</strong> ${m.message} <span class="text-xs text-gray-500">${m.time}</span>`;
          chatBox.appendChild(div);
        });
        chatBox.scrollTop = chatBox.scrollHeight;
      }
    } catch (e) { }
  }

  chatSend.addEventListener('click', async () => {
    const msg = chatInput.value.trim();
    if (!msg) return;
    try {
      const res = await fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: msg }) });
      const data = await res.json();
      if (data.ok) {
        chatInput.value = '';
        loadChat();
      }
    } catch (e) { }
  });

  chatInput.addEventListener('keypress', e => {
    if (e.key === 'Enter') chatSend.click();
  });

  async function heartbeat() {
    try {
      const res = await fetch('/api/heartbeat', { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        onlineCount.innerText = `Online: ${data.online}`;
      }
    } catch (e) { }
  }
  setInterval(heartbeat, 30000);
  heartbeat();

  themeBtn.addEventListener('click', () => {
    document.body.classList.toggle('dark-mode');
  });

  function updateTime() {
    const now = new Date();
    timeNow.innerText = now.toLocaleString('ru-RU');
  }
  setInterval(updateTime, 1000);
  updateTime();

  loadRecent();
  loadChat();
  setInterval(loadChat, 5000);
  animate();
  updateLikes();
</script>

</body>
</html>
"""

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
