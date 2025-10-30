# app.py — единый файл (исправленный + новые функции)
import os
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta
from flask import Flask, request, redirect, session, send_from_directory, jsonify, render_template_string, url_for
from werkzeug.utils import secure_filename
from gtts import gTTS

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

# ========== БД: helper (каждый запрос новый коннект) ==========
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    c = db.cursor()
    # users: добавим banned и last_seen при миграциях ниже если их нет
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
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
    # ensure single row in state
    c.execute("INSERT OR IGNORE INTO state (id, volume) VALUES (1, 0.6)")
    db.commit()
    db.close()

# добавим колонки, если старые бд не содержат
def migrate_db():
    conn = get_db()
    cur = conn.cursor()
    # get current columns for users
    try:
        cur.execute("PRAGMA table_info(users)")
        cols = [r['name'] for r in cur.fetchall()]
        if 'banned' not in cols:
            try:
                cur.execute("ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0")
            except:
                pass
        if 'last_seen' not in cols:
            try:
                cur.execute("ALTER TABLE users ADD COLUMN last_seen TEXT DEFAULT NULL")
            except:
                pass
    except Exception:
        pass
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
    # хотя бы одна буква и цифра
    if not re.search(r'[A-Za-zА-Яа-я]', password) or not re.search(r'\d', password):
        return "Пароль должен содержать хотя бы одну букву и одну цифру."
    return None

def log_action(level, message):
    # простая логировка в файл
    try:
        with open(os.path.join(BASE_DIR, "radio.log"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.utcnow().isoformat()}] {level.upper()}: {message}\n")
    except:
        pass

# ========== Голосовое приветствие ==========
def generate_greeting(display_name, radio_name="Радио Фонтан"):
    """Сгенерировать голосовое приветствие и вернуть имя файла (уникальное)"""
    text = f"Вас приветствует {radio_name}. Сейчас играет: {display_name}. Приятного прослушивания!"
    t = int(time.time() * 1000)
    safe_name = f"greeting_{t}.mp3"
    path = os.path.join(VOICE_FOLDER, safe_name)
    try:
        tts = gTTS(text=text, lang='ru')
        tts.save(path)
    except Exception as e:
        log_action("error", f"gTTS failed: {e}")
        # fallback - пустой файл
        safe_name = "greeting_empty.mp3"
        path = os.path.join(VOICE_FOLDER, safe_name)
        if not os.path.exists(path):
            open(path, "wb").close()
    # очистка старых голосовых файлов (оставляем 12 последних)
    try:
        files = [f for f in os.listdir(VOICE_FOLDER) if f.startswith("greeting_")]
        files_sorted = sorted(files, key=lambda x: os.path.getmtime(os.path.join(VOICE_FOLDER, x)), reverse=True)
        for old in files_sorted[12:]:
            try:
                os.remove(os.path.join(VOICE_FOLDER, old))
            except:
                pass
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
    # log history in file
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
        error = validate_registration(username, password)
        if error:
            pass
        else:
            try:
                conn = get_db()
                conn.execute("INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)",
                           (username, password, datetime.utcnow().isoformat()))
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
            # check banned column safely
            banned = row['banned'] if 'banned' in row.keys() else 0
            if banned:
                conn.close()
                return render_template_string(REG_TEMPLATE_LOGIN, error="Ваш аккаунт заблокирован.")
            session['username'] = username
            session['is_admin'] = False
            # update last_seen
            try:
                conn.execute("UPDATE users SET last_seen = ? WHERE username = ?", (datetime.utcnow().isoformat(), username))
                conn.commit()
            except:
                pass
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

# ========== Статические файлы ==========
@app.route('/music/<path:filename>')
def music_file(filename):
    # prevent directory traversal by secure filename usage in upload
    return send_from_directory(MUSIC_FOLDER, filename)

@app.route('/voice/<path:filename>')
def voice_file(filename):
    return send_from_directory(VOICE_FOLDER, filename)

# ========== Middleware-like helpers ==========
def admin_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get('is_admin'):
            return redirect(url_for('login'))
        return fn(*a, **kw)
    return wrapper

# ========== Admin panel ==========
@app.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin_panel():
    message = None
    if request.method == 'POST':
        # upload
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
        # delete
        if request.form.get('delete_id'):
            try:
                tid = int(request.form.get('delete_id'))
                conn = get_db()
                row = conn.execute("SELECT filename FROM tracks WHERE id = ?", (tid,)).fetchone()
                if row:
                    fname = row['filename']
                    path = os.path.join(MUSIC_FOLDER, fname)
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                    except:
                        pass
                    conn.execute("DELETE FROM tracks WHERE id = ?", (tid,))
                    conn.commit()
                    message = "Трек удалён."
                    log_action("info", f"Admin deleted track {fname}")
                conn.close()
            except Exception as e:
                log_action("error", f"Delete track error: {e}")
        # set current
        if request.form.get('set_current'):
            try:
                tid = int(request.form.get('set_current'))
                display = request.form.get('current_display') or None
                greeting = set_current_track(tid, display)
                message = f"Текущий трек установлен. Приветствие: {greeting}"
            except Exception as e:
                message = "Ошибка установки текущего трека."
                log_action("error", f"Set current error: {e}")
        # set volume
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

        # ban/unban
        if request.form.get('ban_user'):
            uname = request.form.get('ban_user')
            try:
                conn = get_db()
                conn.execute("ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0")
            except:
                pass
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

        # process report
        if request.form.get('process_report'):
            rid = int(request.form.get('process_report'))
            conn = get_db()
            conn.execute("UPDATE reports SET processed = 1 WHERE id = ?", (rid,))
            conn.commit()
            conn.close()
            message = "Жалоба отмечена как обработанная."

    conn = get_db()
    tracks = conn.execute("SELECT * FROM tracks ORDER BY uploaded_at DESC").fetchall()
    # users table may not have last_seen/banned columns in old DB; handle safely
    try:
        users = conn.execute("SELECT username, banned, last_seen FROM users ORDER BY created_at DESC").fetchall()
    except sqlite3.OperationalError:
        # fallback: select only username
        users = conn.execute("SELECT username FROM users ORDER BY rowid DESC").fetchall()
    state = conn.execute("SELECT * FROM state WHERE id = 1").fetchone()
    # online: last_seen within 2 minutes
    online = []
    now = datetime.utcnow()
    for u in users:
        if 'last_seen' in u.keys() and u['last_seen']:
            try:
                t = datetime.fromisoformat(u['last_seen'])
                if now - t <= timedelta(minutes=2):
                    online.append(u['username'])
            except:
                pass
    # reports and likes stats
    reports = conn.execute("SELECT * FROM reports ORDER BY created_at DESC LIMIT 50").fetchall()
    likes_stats = conn.execute("""SELECT t.id, t.display_name, 
                                 (SELECT COUNT(*) FROM likes l WHERE l.track_id = t.id) AS likes
                                 FROM tracks t ORDER BY likes DESC""").fetchall()
    conn.close()
    return render_template_string(ADMIN_TEMPLATE, tracks=tracks, message=message, state=state, users=users, online=online, reports=reports, likes_stats=likes_stats)

# ========== API: next track ==========
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
def api_like():
    if 'username' not in session:
        return jsonify({"ok": False, "error": "Неавторизован"}), 401
    data = request.json or {}
    tid = int(data.get('track_id') or 0)
    if tid <= 0:
        return jsonify({"ok": False, "error": "Invalid track_id"}), 400
    conn = get_db()
    # prevent duplicate like by same user
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

# ========== API: report track ==========
@app.route('/api/report', methods=['POST'])
def api_report():
    if 'username' not in session:
        return jsonify({"ok": False, "error": "Неавторизован"}), 401
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

# ========== API: heartbeat (онлайн) ==========
@app.route('/api/heartbeat', methods=['POST'])
def api_heartbeat():
    if 'username' in session:
        try:
            conn = get_db()
            conn.execute("UPDATE users SET last_seen = ? WHERE username = ?", (datetime.utcnow().isoformat(), session['username']))
            conn.commit()
            conn.close()
            return jsonify({"ok": True})
        except:
            return jsonify({"ok": False}), 500
    return jsonify({"ok": False}), 401

# ========== API: recent tracks & likes ==========
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
def radio_page():
    if 'username' not in session:
        return redirect(url_for('login'))
    # check ban
    conn = get_db()
    rowu = conn.execute("SELECT * FROM users WHERE username = ?", (session['username'],)).fetchone()
    if rowu and 'banned' in rowu.keys() and rowu['banned']:
        conn.close()
        session.clear()
        return "Ваш аккаунт заблокирован.", 403
    # ensure there is current
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
    # compute likes for current
    conn2 = get_db()
    likes_count = conn2.execute("SELECT COUNT(*) as c FROM likes WHERE track_id = ?", (track_row['id'],)).fetchone()['c']
    conn2.close()
    # pass timestamp to bust cache for music (music file may be changed by upload)
    ts = int(time.time() * 1000)
    return render_template_string(RADIO_TEMPLATE,
                                  username=session.get('username'),
                                  is_admin=session.get('is_admin', False),
                                  track_id=track_row['id'],
                                  track_filename=track_row['filename'],
                                  track_display=state['current_display_name'] or track_row['display_name'],
                                  voice_file=None,
                                  volume=state['volume'],
                                  likes_count=likes_count,
                                  ts=ts)

# ========== Templates (встроенные) ==========
REG_TEMPLATE_LOGIN = """
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Вход - Радио Фонтан</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="min-h-screen bg-gradient-to-r from-purple-700 to-indigo-700 flex items-center justify-center">
  <div class="bg-white p-8 rounded-3xl shadow-2xl w-96">
    <h1 class="text-2xl font-bold mb-4 text-center">Вход — Радио Фонтан</h1>
    {% if error %}<p class="text-red-600 text-center">{{ error }}</p>{% endif %}
    <form method="POST" class="flex flex-col gap-3">
      <input name="username" placeholder="Ник" required class="p-2 border rounded-lg" />
      <input name="password" type="password" placeholder="Пароль" required class="p-2 border rounded-lg" />
      <button class="bg-indigo-600 text-white p-2 rounded-lg mt-2">Войти</button>
    </form>
    <p class="text-sm mt-4 text-center">Нет аккаунта? <a href="/register" class="text-indigo-600">Регистрация</a></p>
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
</head>
<body class="min-h-screen bg-gradient-to-r from-purple-700 to-indigo-700 flex items-center justify-center">
  <div class="bg-white p-8 rounded-3xl shadow-2xl w-96">
    <h1 class="text-2xl font-bold mb-4 text-center">Регистрация</h1>
    {% if error %}<p class="text-red-600 text-center">{{ error }}</p>{% endif %}
    <form method="POST" class="flex flex-col gap-3">
      <input name="username" placeholder="Ник (не менее 4 символов)" required class="p-2 border rounded-lg" />
      <input name="password" type="password" placeholder="Пароль (минимум 8, буквы+цифры)" required class="p-2 border rounded-lg" />
      <button class="bg-indigo-600 text-white p-2 rounded-lg mt-2">Создать</button>
    </form>
    <p class="text-sm mt-4 text-center">Уже есть аккаунт? <a href="/" class="text-indigo-600">Войти</a></p>
  </div>
</body>
</html>
"""

ADMIN_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Админ — Радио Фонтан</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="min-h-screen bg-gradient-to-r from-indigo-700 to-purple-700 p-8">
  <div class="max-w-6xl mx-auto bg-white p-6 rounded-3xl shadow-2xl">
    <div class="flex justify-between items-center mb-4">
      <h1 class="text-2xl font-bold">Админская панель — Радио Фонтан</h1>
      <div>
        <a href="/radio" class="text-indigo-600 mr-4">На радио</a>
        <a href="/logout" class="text-red-600">Выйти</a>
      </div>
    </div>
    {% if message %}<p class="text-green-600 mb-3">{{ message }}</p>{% endif %}

    <div class="grid grid-cols-2 gap-6">
      <div class="p-4 border rounded">
        <h2 class="font-semibold mb-2">Загрузить трек (MP3)</h2>
        <form method="POST" enctype="multipart/form-data" class="flex gap-2 items-center">
          <input type="file" name="track" accept=".mp3" class="border p-2 rounded" />
          <input type="text" name="display_name" placeholder="Название (опционально)" class="p-2 border rounded" />
          <button class="bg-indigo-600 text-white p-2 rounded">Загрузить</button>
        </form>
      </div>

      <div class="p-4 border rounded">
        <h2 class="font-semibold mb-2">Громкость по умолчанию</h2>
        <form method="POST" class="flex items-center gap-2">
          <input type="number" name="set_volume" min="0" max="1" step="0.05" value="{{ state['volume'] }}" class="p-1 border rounded w-24" />
          <button class="bg-indigo-600 text-white px-3 py-1 rounded">Сохранить</button>
        </form>
      </div>
    </div>

    <section class="mt-6">
      <h2 class="font-semibold mb-2">Кто слушает сейчас</h2>
      {% if online %}
        <ul>
        {% for u in online %}
          <li>{{ u }}</li>
        {% endfor %}
        </ul>
      {% else %}
        <p>Никто не слушает сейчас.</p>
      {% endif %}
    </section>

    <section class="mt-6">
      <h2 class="font-semibold mb-2">Пользователи</h2>
      <table class="w-full text-left">
        <thead><tr><th>Ник</th><th>Бан</th><th>Last seen</th><th>Действия</th></tr></thead>
        <tbody>
        {% for u in users %}
          <tr class="border-t">
            <td class="py-2">{{ u['username'] }}</td>
            <td class="py-2">{{ u['banned'] if 'banned' in u.keys() else '-' }}</td>
            <td class="py-2">{{ u['last_seen'] if 'last_seen' in u.keys() else '-' }}</td>
            <td class="py-2">
              <form method="POST" style="display:inline">
                {% if 'banned' in u.keys() and u['banned'] %}
                  <input type="hidden" name="unban_user" value="{{ u['username'] }}" />
                  <button class="px-2 py-1 rounded bg-green-500 text-white">Разбан</button>
                {% else %}
                  <input type="hidden" name="ban_user" value="{{ u['username'] }}" />
                  <button class="px-2 py-1 rounded bg-red-500 text-white">Забанить</button>
                {% endif %}
              </form>
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </section>

    <section class="mt-6">
      <h2 class="font-semibold mb-2">Статистика лайков</h2>
      <ul>
      {% for s in likes_stats %}
        <li>{{ s['display_name'] }} — {{ s['likes'] }} ❤</li>
      {% endfor %}
      </ul>
    </section>

    <section class="mt-6">
      <h2 class="font-semibold mb-2">Жалобы (reports)</h2>
      <table class="w-full text-left">
        <thead><tr><th>id</th><th>Пользователь</th><th>Причина</th><th>Трек</th><th>Когда</th><th>Действие</th></tr></thead>
        <tbody>
        {% for r in reports %}
          <tr class="border-t">
            <td>{{ r['id'] }}</td>
            <td>{{ r['username'] }}</td>
            <td>{{ r['reason'] }}</td>
            <td>{{ r['track_id'] }}</td>
            <td>{{ r['created_at'] }}</td>
            <td>
              {% if r['processed'] == 0 %}
              <form method="POST" style="display:inline">
                <input type="hidden" name="process_report" value="{{ r['id'] }}" />
                <button class="px-2 py-1 rounded bg-indigo-600 text-white">Отметить обработанной</button>
              </form>
              {% else %}
                <span>Обработана</span>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </section>

    <section class="mt-6">
      <h2 class="font-semibold mb-2">Список треков</h2>
      <table class="w-full text-left">
        <thead><tr><th>id</th><th>Название</th><th>Файл</th><th>Действия</th></tr></thead>
        <tbody>
        {% for t in tracks %}
          <tr class="border-t">
            <td class="py-2">{{ t['id'] }}</td>
            <td class="py-2">{{ t['display_name'] }}</td>
            <td class="py-2">{{ t['filename'] }}</td>
            <td class="py-2">
              <form method="POST" style="display:inline">
                <input type="hidden" name="set_current" value="{{ t['id'] }}" />
                <input type="text" name="current_display" placeholder="Имя для эфира" class="p-1 border rounded text-sm" />
                <button class="bg-green-500 text-white px-3 py-1 rounded ml-2">Установить</button>
              </form>
              <form method="POST" style="display:inline" onsubmit="return confirm('Удалить?')">
                <input type="hidden" name="delete_id" value="{{ t['id'] }}" />
                <button class="bg-red-500 text-white px-3 py-1 rounded ml-2">Удалить</button>
              </form>
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </section>

  </div>
</body>
</html>
"""

RADIO_TEMPLATE_EMPTY = """
<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><script src="https://cdn.tailwindcss.com"></script></head>
<body class="min-h-screen bg-gradient-to-r from-purple-700 to-indigo-700 flex items-center justify-center">
  <div class="bg-white p-8 rounded-3xl shadow-2xl text-center w-96">
    <h1 class="text-2xl font-bold mb-4">Радио Фонтан</h1>
    <p>Пока нет загруженных треков.</p>
    <p class="mt-4"><a href="/logout" class="text-red-600">Выйти</a></p>
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
    .bar { width:6px; height:20px; margin:0 2px; display:inline-block; background:linear-gradient(180deg,#34d399,#06b6d4); border-radius:2px; transform-origin: bottom; transition: height 0.08s linear; }
    .btn { padding:8px 12px; border-radius:10px; font-weight:600; }
  </style>
</head>
<body class="min-h-screen bg-gradient-to-r from-indigo-700 to-purple-700 flex items-center justify-center">
  <div class="bg-white p-6 rounded-3xl shadow-2xl w-11/12 max-w-4xl">
    <div class="flex justify-between items-center mb-4">
      <div>
        <h1 class="text-2xl font-bold">Радио Фонтан</h1>
        <p class="text-sm text-gray-600">Привет, {{ username }} {% if is_admin %}(админ){% endif %}</p>
      </div>
      <div class="text-right">
        <p class="text-sm">Сейчас играет:</p>
        <p id="currName" class="font-semibold">{{ track_display }}</p>
        <p id="timeNow" class="text-xs text-gray-500"></p>
      </div>
    </div>

    <div class="mb-4 flex gap-4 items-center">
      <button id="startBtn" class="btn bg-indigo-600 text-white">Запустить радио</button>
      <button id="nextBtn" class="btn bg-gray-200">Следующий</button>
      <label class="text-sm">Громкость</label>
      <input id="volumeSlider" type="range" min="0" max="1" step="0.01" value="{{ volume }}" />
      <button id="likeBtn" class="btn bg-rose-500 text-white">❤ Лайк (<span id="likesCount">{{ likes_count }}</span>)</button>
      <button id="reportBtn" class="btn bg-yellow-400">Пожаловаться</button>
      <button id="themeBtn" class="btn bg-gray-800 text-white">Тема</button>
      <span id="onlineCount" class="text-sm text-gray-600 ml-4"></span>
    </div>

    <div class="mb-4">
      <div id="visual" class="flex items-end h-28"></div>
    </div>

    <div class="mb-3">
      <p id="status" class="text-gray-600">Статус: готов</p>
    </div>

    <div class="mb-4">
      <h3 class="font-semibold mb-2">Последние треки</h3>
      <ul id="recentList" class="list-disc pl-6 text-sm text-gray-700"></ul>
    </div>

    <div class="flex justify-between items-center">
      <div>
        <a href="/logout" class="text-red-600">Выйти</a>
        {% if is_admin %}<a href="/admin" class="ml-4 text-indigo-600">Админ</a>{% endif %}
      </div>
      <div id="likesInfo" class="text-sm text-gray-600"></div>
    </div>

    <!-- Скрытые аудиоэлементы -->
    <audio id="music" crossorigin="anonymous" src="/music/{{ track_filename }}?t={{ ts }}"></audio>
    <audio id="greeting" src=""></audio>

  </div>

<script>
  // --- Элементы ---
  const startBtn = document.getElementById('startBtn');
  const music = document.getElementById('music');
  const greeting = document.getElementById('greeting');
  const status = document.getElementById('status');
  const visual = document.getElementById('visual');
  const volumeSlider = document.getElementById('volumeSlider');
  const nextBtn = document.getElementById('nextBtn');
  const likeBtn = document.getElementById('likeBtn');
  const reportBtn = document.getElementById('reportBtn');
  const themeBtn = document.getElementById('themeBtn');
  const recentList = document.getElementById('recentList');
  const currName = document.getElementById('currName');
  const likesCountSpan = document.getElementById('likesCount');
  const onlineCount = document.getElementById('onlineCount');
  const timeNow = document.getElementById('timeNow');

  // bars
  const BAR_COUNT = 20;
  for (let i=0;i<BAR_COUNT;i++){
    const b = document.createElement('div');
    b.className = 'bar';
    b.style.height = (5 + Math.random()*20) + 'px';
    visual.appendChild(b);
  }
  const bars = Array.from(document.querySelectorAll('.bar'));

  // audio context
  let audioCtx, analyser, sourceNode;
  function setupAudioContext() {
    if (audioCtx) return;
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    try {
      sourceNode = audioCtx.createMediaElementSource(music);
      sourceNode.connect(analyser);
      analyser.connect(audioCtx.destination);
    } catch(e){
      console.warn("MediaElementSource error", e);
    }
  }

  function animate() {
    if (!analyser) { requestAnimationFrame(animate); return; }
    const data = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(data);
    for (let i=0;i<BAR_COUNT;i++){
      const v = data[i*2] || 0;
      const h = Math.max(4, (v/255)*120);
      bars[i].style.height = h + 'px';
      bars[i].style.opacity = (0.3 + v/255*0.7);
      bars[i].style.transform = 'scaleY(' + (0.5 + v/255*1.5) + ')';
    }
    requestAnimationFrame(animate);
  }

  // flags
  let greetingPlaying = false;
  let loadingNext = false;

  // safely assign onended once
  music.onended = async function(){
    status.innerText = "💤 Трек закончился — запрашиваем следующий...";
    await fetchNext();
  };

  greeting.onended = function(){
    greetingPlaying = false;
    const target = parseFloat(volumeSlider.value);
    status.innerText = "▶ Сейчас играет: " + currName.innerText;
    let t = 0;
    const steps = 20;
    const step = (target - music.volume) / steps;
    const intr = setInterval(()=>{
      t++;
      music.volume = Math.max(0, Math.min(1, music.volume + step));
      if (t>=steps){ clearInterval(intr); music.volume = target; }
    }, 80);
  };

  async function fetchNext(){
    if (loadingNext) return;
    loadingNext = true;
    nextBtn.disabled = true;
    try {
      const res = await fetch('/api/next', {method:'POST'});
      const data = await res.json();
      if (data.ok){
        // stop current playback
        try { greeting.pause(); greeting.currentTime = 0; } catch(e){}
        try { music.pause(); music.currentTime = 0; } catch(e){}
        // set new src with ts to bust cache
        greeting.src = '/voice/' + data.greeting + '?t=' + data.ts;
        music.src = '/music/' + data.filename + '?t=' + data.ts;
        currName.innerText = data.display_name;
        status.innerText = "▶ Загружаем новый трек: " + data.display_name;
        music.load(); greeting.load();
        music.volume = 0.02;
        try { await music.play(); } catch(e){}
        try { await greeting.play(); greetingPlaying = true; } catch(e){}
        loadRecent();
        updateLikes();
      } else {
        status.innerText = "Ошибка сервера при получении следующего трека";
      }
    } catch(e){
      console.error(e);
      status.innerText = "Сетевая ошибка при получении следующего трека";
    } finally {
      loadingNext = false;
      nextBtn.disabled = false;
    }
  }

  nextBtn.addEventListener('click', async ()=>{
    status.innerText = "Запрос следующего...";
    await fetchNext();
  });

  // start radio
  startBtn.addEventListener('click', async ()=>{
    setupAudioContext();
    if (audioCtx && audioCtx.state === 'suspended') await audioCtx.resume();
    const ts = Date.now();
    // append ts to force reload if needed
    music.src = music.src.split('?')[0] + '?t=' + ts;
    music.volume = 0.02;
    greeting.volume = 1.0;
    status.innerText = "▶ Запуск: приветствие + тихая музыка";
    try { await music.play(); } catch(e){}
    try { await greeting.play(); greetingPlaying = true; } catch(e){}
    animate();
  });

  // volume control
  volumeSlider.addEventListener('input', ()=>{
    const v = parseFloat(volumeSlider.value);
    if (!greetingPlaying){
      music.volume = v;
    }
  });

  // like
  likeBtn.addEventListener('click', async ()=>{
    const curTrackId = {{ track_id }};
    try {
      const res = await fetch('/api/like', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({track_id: curTrackId})});
      const data = await res.json();
      if (data.ok){
        status.innerText = "Спасибо за лайк!";
        updateLikes();
      } else {
        status.innerText = data.error || "Не получилось поставить лайк";
      }
    } catch(e){
      status.innerText = "Ошибка сети при лайке";
    }
  });

  async function updateLikes(){
    try {
      const res = await fetch('/api/recent');
      const data = await res.json();
      if (data.ok){
        const cur = data.recent.find(r => r.track_id === {{ track_id }});
        if (cur) likesCountSpan.innerText = cur.likes;
      }
    } catch(e){}
  }

  // report
  reportBtn.addEventListener('click', async ()=>{
    const reason = prompt("Опишите причину жалобы (коротко):");
    if (!reason) return;
    try {
      const res = await fetch('/api/report', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({track_id: {{ track_id }}, reason})});
      const data = await res.json();
      if (data.ok){
        alert("Жалоба отправлена. Спасибо.");
      } else {
        alert("Ошибка: " + (data.error || 'unknown'));
      }
    } catch(e){
      alert("Ошибка сети");
    }
  });

  // recent
  async function loadRecent(){
    try {
      const res = await fetch('/api/recent');
      const data = await res.json();
      if (data.ok){
        recentList.innerHTML = '';
        data.recent.forEach(r=>{
          const li = document.createElement('li');
          li.innerText = `${r.uploaded_at} — ${r.display_name} (${r.likes}❤)`;
          recentList.appendChild(li);
        });
      }
    } catch(e){}
  }

  // heartbeat for online marking
  async function heartbeat(){
    try {
      await fetch('/api/heartbeat', {method:'POST'});
      // also get online count via recent to keep it simple
      const res = await fetch('/api/recent');
      const data = await res.json();
      // online count will be fetched from admin only; show small indicator via separate endpoint? keep it simple:
      // instead, fetch /admin_online_count (not implemented) - so we just show "Online: —"
    } catch(e){}
  }
  setInterval(heartbeat, 30*1000);
  heartbeat();

  // theme toggle (local)
  themeBtn.addEventListener('click', ()=>{
    document.body.classList.toggle('dark-mode');
    if (document.body.classList.contains('dark-mode')){
      document.body.style.background = 'linear-gradient(135deg, #0f172a, #111827)';
    } else {
      document.body.style.background = '';
    }
  });

  // show time
  function updateTime(){
    const now = new Date();
    timeNow.innerText = now.toLocaleString();
  }
  setInterval(updateTime, 1000);
  updateTime();

  // initial load
  loadRecent();
  animate();
  updateLikes();
</script>

</body>
</html>
"""

# ========== Запуск ==========
if __name__ == '__main__':
    # Запуск dev сервера
    app.run(debug=True, host='0.0.0.0', port=5000)
