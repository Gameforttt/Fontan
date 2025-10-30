@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if not session.get('admin'):
        return redirect('/admin_login')

    if request.method == 'POST':
        if 'track' not in request.files:
            return "Файл не выбран"
        file = request.files['track']
        if file.filename.endswith('.mp3'):
            file.save(os.path.join(music_folder, file.filename))
            return redirect('/admin')
        else:
            return "Неверный формат файла"
    
    tracks = os.listdir(music_folder)
    track_list_html = "<ul>" + "".join(f"<li>{t}</li>" for t in tracks) + "</ul>"

    return f'''
        <h1>Админ панель</h1>
        <h2>Загрузить новый трек</h2>
        <form method="POST" enctype="multipart/form-data">
            <input type="file" name="track" accept="audio/mp3" required>
            <input type="submit" value="Загрузить">
        </form>
        <h2>Список треков:</h2>
        {track_list_html}
        <a href="/logout">Выйти</a>
    '''

@app.route('/logout')
def logout():
    session.pop('admin', None)
    return redirect('/')
