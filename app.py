import os
import random
import re
import sqlite3
from datetime import datetime
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

# ========== БД ==========
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

db = get_db()
def init_db():
    c = db.cursor()
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
    # ensure single row in state
    c.execute("INSERT OR IGNORE INTO state (id, volume) VALUES (1, 0.6)")
    db.commit()

init_db()

# ========== Вспомогательные функции ==========
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

def generate_greeting(display_name, radio_name="Радио Фонтан"):
    """Сгенерировать голосовое приветствие и вернуть имя файла"""
    text = f"Вас приветствует радио Фонтан. Сейчас играет: {display_name}. Приятного прослушивания!"
    # имя файла генерируем по времени, чтобы не кэшировалось
    safe_name = "greeting.mp3"
    path = os.path.join(VOICE_FOLDER, safe_name)
    tts = gTTS(text=text, lang='ru')
    tts.save(path)
    return safe_name

def set_current_track(track_id, display_name=None):
    c = db.cursor()
    if display_name is None:
        # взять display_name из таблицы tracks
        row = c.execute("SELECT display_name FROM tracks WHERE id = ?", (track_id,)).fetchone()
        display_name = row['display_name'] if row else ''
    c.execute("UPDATE state SET current_track_id = ?, current_display_name = ? WHERE id = 1",
              (track_id, display_name))
    db.commit()
    # сгенерировать приветствие
    greeting_file = generate_greeting(display_name)
    return greeting_file

def get_state():
    c = db.cursor()
    row = c.execute("SELECT * FROM state WHERE id = 1").fetchone()
    return row

# ========== Маршруты auth и регистрация ==========
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
                db.execute("INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)",
                           (username, password, datetime.utcnow().isoformat()))
                db.commit()
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
        row = db.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password)).fetchone()
        if row:
            session['username'] = username
            session['is_admin'] = False
            return redirect('/radio')
        else:
            error = "Неверный логин или пароль."
    return render_template_string(REG_TEMPLATE_LOGIN, error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ========== Статика (файлы) ==========
@app.route('/music/<path:filename>')
def music_file(filename):
    return send_from_directory(MUSIC_FOLDER, filename)

@app.route('/voice/<path:filename>')
def voice_file(filename):
    return send_from_directory(VOICE_FOLDER, filename)

# ========== Админ-панель ==========
def admin_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get('is_admin'):
            return redirect(url_for('login'))
        return fn(*a, **kw)
    return wrapper

@app.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin_panel():
    message = None
    if request.method == 'POST':
        # загрузка файла
        if 'track' in request.files:
            f = request.files['track']
            if f and is_allowed(f.filename):
                filename = secure_filename(f.filename)
                # если файл с именем уже есть, добавляем случайный суффикс
                dest = os.path.join(MUSIC_FOLDER, filename)
                if os.path.exists(dest):
                    base, ext = os.path.splitext(filename)
                    filename = f"{base}_{random.randint(1000,9999)}{ext}"
                    dest = os.path.join(MUSIC_FOLDER, filename)
                f.save(dest)
                display_name = request.form.get('display_name') or filename
                db.execute("INSERT INTO tracks (filename, display_name, uploaded_at) VALUES (?, ?, ?)",
                           (filename, display_name, datetime.utcnow().isoformat()))
                db.commit()
                message = "Трек загружен."
            else:
                message = "Неверный файл (только .mp3)."
        # удалить
        if request.form.get('delete_id'):
            tid = int(request.form.get('delete_id'))
            row = db.execute("SELECT filename FROM tracks WHERE id = ?", (tid,)).fetchone()
            if row:
                fname = row['filename']
                path = os.path.join(MUSIC_FOLDER, fname)
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except:
                    pass
                db.execute("DELETE FROM tracks WHERE id = ?", (tid,))
                db.commit()
                message = "Трек удалён."
        # установить текущий
        if request.form.get('set_current'):
            tid = int(request.form.get('set_current'))
            display = request.form.get('current_display') or None
            greeting = set_current_track(tid, display)
            message = f"Текущий трек установлен. Приветствие: {greeting}"
        # установить громкость по умолчанию
        if request.form.get('set_volume'):
            v = float(request.form.get('set_volume'))
            db.execute("UPDATE state SET volume = ? WHERE id = 1", (max(0, min(1, v)),))
            db.commit()
            message = "Громкость сохранена."

    tracks = db.execute("SELECT * FROM tracks ORDER BY uploaded_at DESC").fetchall()
    state = get_state()
    return render_template_string(ADMIN_TEMPLATE, tracks=tracks, message=message, state=state)

# ========== API: следующий трек (сервер выбирает новый) ==========
@app.route('/api/next', methods=['POST'])
def api_next():
    # выберем случайный трек (кроме текущего, по возможности)
    rows = db.execute("SELECT id FROM tracks").fetchall()
    if not rows:
        return jsonify({"ok": False, "error": "Нет треков"}), 400
    ids = [r['id'] for r in rows]
    state = get_state()
    current = state['current_track_id']
    candidates = [i for i in ids if i != current]
    if not candidates:
        candidates = ids
    new_id = random.choice(candidates)
    greeting = set_current_track(new_id)
    row = db.execute("SELECT filename FROM tracks WHERE id = ?", (new_id,)).fetchone()
    filename = row['filename']
    display_name = db.execute("SELECT display_name FROM tracks WHERE id = ?", (new_id,)).fetchone()['display_name']
    return jsonify({"ok": True, "id": new_id, "filename": filename, "display_name": display_name, "greeting": greeting})

# ========== Радио - публичная страница ==========
@app.route('/radio')
def radio_page():
    if 'username' not in session:
        return redirect(url_for('login'))
    # получить current
    state = get_state()
    if state['current_track_id'] is None:
        # если нет текущего — выбрать случайный (если есть треки)
        row = db.execute("SELECT id FROM tracks ORDER BY RANDOM() LIMIT 1").fetchone()
        if row:
            set_current_track(row['id'])
            state = get_state()
    if state['current_track_id'] is None:
        # нет треков
        return render_template_string(RADIO_TEMPLATE_EMPTY)
    track_row = db.execute("SELECT * FROM tracks WHERE id = ?", (state['current_track_id'],)).fetchone()
    if not track_row:
        return render_template_string(RADIO_TEMPLATE_EMPTY)
    # передать данные в шаблон
    return render_template_string(RADIO_TEMPLATE,
                                  username=session.get('username'),
                                  is_admin=session.get('is_admin', False),
                                  track_filename=track_row['filename'],
                                  track_display=state['current_display_name'] or track_row['display_name'],
                                  voice_file="greeting.mp3",
                                  volume=state['volume'])

# ========== Шаблоны (строки) ==========
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
  <div class="max-w-4xl mx-auto bg-white p-6 rounded-3xl shadow-2xl">
    <div class="flex justify-between items-center mb-4">
      <h1 class="text-2xl font-bold">Админская панель — Радио Фонтан</h1>
      <div>
        <a href="/radio" class="text-indigo-600 mr-4">На радио</a>
        <a href="/logout" class="text-red-600">Выйти</a>
      </div>
    </div>
    {% if message %}<p class="text-green-600 mb-3">{{ message }}</p>{% endif %}

    <section class="mb-6">
      <h2 class="font-semibold mb-2">Загрузить трек (MP3)</h2>
      <form method="POST" enctype="multipart/form-data" class="flex gap-2 items-center">
        <input type="file" name="track" accept=".mp3" class="border p-2 rounded" />
        <input type="text" name="display_name" placeholder="Название (опционально)" class="p-2 border rounded" />
        <button class="bg-indigo-600 text-white p-2 rounded">Загрузить</button>
      </form>
    </section>

    <section class="mb-6">
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

    <section>
      <h2 class="font-semibold mb-2">Громкость по умолчанию</h2>
      <form method="POST" class="flex items-center gap-2">
        <input type="number" name="set_volume" min="0" max="1" step="0.05" value="{{ state['volume'] }}" class="p-1 border rounded w-24" />
        <button class="bg-indigo-600 text-white px-3 py-1 rounded">Сохранить</button>
      </form>
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
    /* простая стилизация полос */
    .bar { width:6px; height:20px; margin:0 2px; display:inline-block; background:linear-gradient(180deg,#34d399,#06b6d4); border-radius:2px; transform-origin: bottom; }
  </style>
</head>
<body class="min-h-screen bg-gradient-to-r from-indigo-700 to-purple-700 flex items-center justify-center">
  <div class="bg-white p-6 rounded-3xl shadow-2xl w-11/12 max-w-3xl">
    <div class="flex justify-between items-center mb-4">
      <div>
        <h1 class="text-2xl font-bold">Радио Фонтан</h1>
        <p class="text-sm text-gray-600">Привет, {{ username }} {% if is_admin %}(админ){% endif %}</p>
      </div>
      <div class="text-right">
        <p class="text-sm">Сейчас играет:</p>
        <p class="font-semibold">{{ track_display }}</p>
      </div>
    </div>

    <div class="mb-4">
      <!-- Кнопка старт (автоплей в браузере часто блокируется) -->
      <div class="flex gap-3 items-center">
        <button id="startBtn" class="bg-indigo-600 text-white px-4 py-2 rounded">Запустить радио</button>
        <label class="text-sm">Громкость музыки</label>
        <input id="volumeSlider" type="range" min="0" max="1" step="0.01" value="{{ volume }}" />
      </div>
    </div>

    <div class="mb-4">
      <!-- визуализация -->
      <div id="visual" class="flex items-end h-28"></div>
    </div>

    <div class="mb-3">
      <p id="status" class="text-gray-600">Статус: готов</p>
    </div>

    <div class="flex justify-between items-center">
      <div>
        <a href="/logout" class="text-red-600">Выйти</a>
        {% if is_admin %}
        <a href="/admin" class="ml-4 text-indigo-600">Админ</a>
        {% endif %}
      </div>
      <div>
        <button id="nextBtn" class="bg-gray-200 px-3 py-1 rounded">Следующий</button>
      </div>
    </div>

    <!-- Скрытые аудиоэлементы - управляем через JS -->
    <audio id="music" crossorigin="anonymous" src="/music/{{ track_filename }}"></audio>
    <audio id="greeting" src="/voice/{{ voice_file }}"></audio>

  </div>

<script>
  // --- Переменные и элементы ---
  const startBtn = document.getElementById('startBtn');
  const music = document.getElementById('music');
  const greeting = document.getElementById('greeting');
  const status = document.getElementById('status');
  const visual = document.getElementById('visual');
  const volumeSlider = document.getElementById('volumeSlider');
  const nextBtn = document.getElementById('nextBtn');

  // создаём полосы для визуализации (20 штук)
  const BAR_COUNT = 20;
  for (let i=0;i<BAR_COUNT;i++){
    const b = document.createElement('div');
    b.className = 'bar';
    b.style.height = (5 + Math.random()*20) + 'px';
    visual.appendChild(b);
  }
  const bars = Array.from(document.querySelectorAll('.bar'));

  // Web Audio API
  let audioCtx, analyser, sourceNode;

  function setupAudioContext() {
    if (audioCtx) return;
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    sourceNode = audioCtx.createMediaElementSource(music);
    sourceNode.connect(analyser);
    analyser.connect(audioCtx.destination);
  }

  function animate() {
    if (!analyser) return;
    const data = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(data);
    // map data to bars
    for (let i=0;i<BAR_COUNT;i++){
      const v = data[i*2] || 0;
      const h = Math.max(4, (v/255)*120);
      bars[i].style.height = h + 'px';
      bars[i].style.opacity = (0.3 + v/255*0.7);
      bars[i].style.transform = 'scaleY(' + (0.5 + v/255*1.5) + ')';
    }
    requestAnimationFrame(animate);
  }

  // логика воспроизведения: при старте воспроизводим greeting и тихую музыку,
  // затем после окончания greeting — увеличиваем громкость до выбранной

  startBtn.addEventListener('click', async ()=>{
    // разблокировать AudioContext на взаимодействие пользователя
    setupAudioContext();
    if (audioCtx.state === 'suspended') await audioCtx.resume();

    // установить начальную громкость музыки очень тихо
    const userVol = parseFloat(volumeSlider.value);
    music.volume = 0.08; // тихо на фоне голоса
    greeting.volume = 1.0;
    status.innerText = "▶ Запуск: голос приветствия + тихая музыка...";
    // play both
    try {
      await music.play();
    } catch(e){
      console.warn("music play blocked:", e);
    }
    try {
      await greeting.play();
    } catch(e){
      console.warn("greeting play blocked:", e);
    }

    // когда greeting закончится — поднять громкость музыки до значения слайдера
    greeting.onended = function(){
      // плавно увеличить громкость
      const target = parseFloat(volumeSlider.value);
      status.innerText = "▶ Сейчас играет: {{ track_display }}";
      // плавное увеличение
      let t = 0;
      const steps = 20;
      const startVol = music.volume;
      const step = (target - startVol) / steps;
      const intr = setInterval(()=>{
        t++;
        music.volume = Math.max(0, Math.min(1, music.volume + step));
        if (t>=steps){ clearInterval(intr); music.volume = target; }
      }, 80);
    };

    // если трек закончился — запросим следующий с сервера
    music.onended = async function(){
      status.innerText = "💤 Трек закончился — ждём серверный следующий...";
      // просим сервер выбрать следующий
      try {
        const res = await fetch('/api/next', {method: 'POST'});
        const data = await res.json();
        if (data.ok){
          // загрузить новый greeting и музыку
          greeting.src = '/voice/' + data.greeting;
          music.src = '/music/' + data.filename;
          // заставим брауз загрузить и воспроизвести: сначала greeting + тихая музыка
          // play music (so analyser works) but keep quiet until greeting ends
          music.load(); greeting.load();
          try { await music.play(); } catch(e){}
          try { await greeting.play(); } catch(e){}
          status.innerText = "▶ Сейчас играет: " + data.display_name;
        } else {
          status.innerText = "Ошибка при получении следующего трека";
        }
      } catch(e){
        console.error(e);
        status.innerText = "Ошибка сети при запросе следующего трека";
      }
    };

    // запустить анимацию визуализации
    animate();
  });

  // регулировка громкости в реальном времени (если greeting ещё играет — оставлять тихо)
  volumeSlider.addEventListener('input', ()=>{
    const v = parseFloat(volumeSlider.value);
    // применяем как "целевую" громкость; если greeting уже завершился — ставим сразу
    if (greeting.paused || greeting.ended){
      music.volume = v;
    }
    // отправим на сервер чтобы сохранить пользовательскую громкость по умолчанию (не обязательно)
    // но тут не шлём - только админ может сохранить по умолчанию.
  });

  // кнопка "Следующий" — принудительно запросить серверный next
  nextBtn.addEventListener('click', async ()=>{
    status.innerText = "Запрос следующего...";
    try {
      const res = await fetch('/api/next', {method:'POST'});
      const data = await res.json();
      if (data.ok){
        greeting.src = '/voice/' + data.greeting;
        music.src = '/music/' + data.filename;
        music.load(); greeting.load();
        try { await music.play(); } catch(e){}
        try { await greeting.play(); } catch(e){}
        status.innerText = "▶ Сейчас играет: " + data.display_name;
      } else {
        status.innerText = "Ошибка: " + (data.error || 'unknown');
      }
    } catch(e){
      status.innerText = "Ошибка сети";
    }
  });
</script>

</body>
</html>
"""

# ========== Запуск ==========
if __name__ == '__main__':
    # запуск dev сервера
    app.run(debug=True, host='0.0.0.0', port=5000)
