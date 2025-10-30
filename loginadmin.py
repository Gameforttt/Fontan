@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect('/admin')
        else:
            return "Неверный пароль"
    return '''
        <form method="POST">
            Пароль администратора: <input type="password" name="password">
            <input type="submit" value="Войти">
        </form>
    '''
