import os
import secrets
import logging
import pandas as pd
import requests
from datetime import datetime
from typing import Optional, Tuple, List

from flask import (Flask, render_template, url_for, flash,
                   redirect, request, send_file, abort)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user,
                         current_user, logout_user, login_required)
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, PasswordField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError
from werkzeug.security import generate_password_hash, check_password_hash
from bs4 import BeautifulSoup

# --- ИНИЦИАЛИЗАЦИЯ И ЛОГИРОВАНИЕ ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(24))

# Настройка БД для Heroku (PostgreSQL) и локально (SQLite)
uri = os.getenv("DATABASE_URL")
if uri and uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = uri or 'sqlite:///bookmarks.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024  # Лимит 2MB

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])


# --- ORM МОДЕЛИ ---

class User(db.Model, UserMixin):
    """Модель пользователя для системы авторизации."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    bookmarks = db.relationship('Bookmark', backref='author', lazy=True)


class Bookmark(db.Model):
    """Модель закладки, связанная с пользователем."""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text, nullable=True)
    tags = db.Column(db.String(200), nullable=True)
    date_posted = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    image_file = db.Column(db.String(40), nullable=False, default='default.png')
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


@login_manager.user_loader
def load_user(user_id: int):
    return User.query.get(int(user_id))


# --- ФОРМЫ (WTFORMS) ---

class RegistrationForm(FlaskForm):
    username = StringField('Имя пользователя', validators=[DataRequired(), Length(min=2, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    confirm_password = PasswordField('Повторите пароль', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Создать аккаунт')

    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user: raise ValidationError('Это имя уже занято.')


class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    submit = SubmitField('Войти')


class BookmarkForm(FlaskForm):
    title = StringField('Заголовок (оставьте пустым для автозаполнения)')
    url = StringField('URL адрес', validators=[DataRequired()])
    description = TextAreaField('Заметки')
    tags = StringField('Теги (через запятую)')
    picture = FileField('Превью (jpg/png)', validators=[FileAllowed(['jpg', 'png'])])
    submit = SubmitField('Сохранить')


class ImportForm(FlaskForm):
    html_file = FileField('HTML файл закладок', validators=[DataRequired()])
    submit = SubmitField('Импортировать')


# --- СЕРВИСНЫЕ ФУНКЦИИ ---

def get_meta_api(target_url: str) -> Tuple[Optional[str], Optional[str]]:
    """Временная заглушка внешнего API для обеспечения мгновенной загрузки сайта."""
    # Мы временно возвращаем None, чтобы Python не ходил в интернет и не вешал сайт
    return None, None



def save_picture(form_picture) -> str:
    """Сохранение загруженного изображения с уникальным именем."""
    random_hex = secrets.token_hex(8)
    _, f_ext = os.path.splitext(form_picture.filename)
    picture_fn = random_hex + f_ext
    picture_path = os.path.join(app.root_path, 'static/uploads', picture_fn)
    form_picture.save(picture_path)
    return picture_fn


# --- МАРШРУТЫ (ROUTES) ---

@app.route("/")
@app.route("/home")
def home():
    page = request.args.get('page', 1, type=int)
    query = request.args.get('q', '')
    tag = request.args.get('tag', '')

    if current_user.is_authenticated:
        # Фильтрация и поиск
        stmt = Bookmark.query.filter_by(author=current_user)
        if tag:
            stmt = stmt.filter(Bookmark.tags.contains(tag))
        if query:
            stmt = stmt.filter(Bookmark.title.contains(query) | Bookmark.description.contains(query))

        bookmarks = stmt.order_by(Bookmark.date_posted.desc()).paginate(page=page, per_page=5)
        return render_template('index.html', bookmarks=bookmarks, tag=tag, query=query)
    return render_template('index.html', bookmarks=None)


@app.route("/register", methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('home'))
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data,
                    password=generate_password_hash(form.password.data))
        db.session.add(user)
        db.session.commit()
        flash('Регистрация успешна!', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', form=form)


@app.route("/login", methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and check_password_hash(user.password, form.password.data):
            login_user(user)
            return redirect(url_for('home'))
        flash('Ошибка входа', 'danger')
    return render_template('login.html', form=form)


@app.route("/bookmark/new", methods=['GET', 'POST'])
@login_required
def new_bookmark():
    form = BookmarkForm()
    if form.validate_on_submit():
        pic = save_picture(form.picture.data) if form.picture.data else 'default.png'

        # Интеграция API, если заголовок не введен
        api_t, api_d = (None, None)
        if not form.title.data:
            api_t, api_d = get_meta_api(form.url.data)

        bookmark = Bookmark(
            title=form.title.data or api_t or "Без названия",
            url=form.url.data,
            description=form.description.data or api_d,
            tags=form.tags.data,
            image_file=pic,
            author=current_user
        )
        db.session.add(bookmark)
        db.session.commit()
        flash('Закладка добавлена!', 'success')
        return redirect(url_for('home'))
    return render_template('create_bookmark.html', form=form, legend='Новая закладка')


@app.route("/bookmark/<int:bookmark_id>/delete", methods=['POST'])
@login_required
def delete_bookmark(bookmark_id):
    bookmark = Bookmark.query.get_or_404(bookmark_id)
    if bookmark.author != current_user: abort(403)
    db.session.delete(bookmark)
    db.session.commit()
    return redirect(url_for('home'))


@app.route("/export")
@login_required
def export_data():
    bookmarks = Bookmark.query.filter_by(author=current_user).all()
    df = pd.DataFrame([{
        'title': b.title, 'url': b.url, 'tags': b.tags
    } for b in bookmarks])
    path = "export.csv"
    df.to_csv(path, index=False)
    return send_file(path, as_attachment=True)


@app.errorhandler(404)
def error_404(error):
    return "Страница не найдена (404)", 404


@app.route("/logout")
@login_required
def logout():
    """Маршрут для выхода пользователя из системы."""
    logout_user()
    flash('Вы успешно вышли из системы.', 'info')
    return redirect(url_for('home'))


# Запуск
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
