import asyncio
import json
import logging
import os
from typing import Dict, Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

# ======================================
#   НАСТРОЙКИ И ЗАГРУЗКА .env
# ======================================

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
REQUIRED_CHANNELS_RAW = os.getenv("REQUIRED_CHANNELS", "")
REQUIRED_CHANNELS = [c.strip() for c in REQUIRED_CHANNELS_RAW.split(",") if c.strip()]

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")  # твой Telegram ID
INVITE_LINK = "https://t.me/+g5TaZCcRaaZkNjFi"    # твоя пригласительная ссылка на канал

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

MOVIES_FILE = "movies.json"

# ======================================
#   ЗАГРУЗКА / СОХРАНЕНИЕ ФИЛЬМОВ
# ======================================

def load_movies() -> Dict[str, str]:
    if os.path.exists(MOVIES_FILE):
        try:
            with open(MOVIES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # на всякий, приводим ключи к str
            return {str(k).upper(): str(v) for k, v in data.items()}
        except Exception as e:
            logging.warning(f"Не удалось загрузить {MOVIES_FILE}: {e}")
    # дефолтные фильмы, если файла нет
    return {
        "A123": "Бойцовский клуб",
        "B415": "Начало",
        "C777": "Матрица",
    }


def save_movies(movies: Dict[str, str]) -> None:
    try:
        with open(MOVIES_FILE, "w", encoding="utf-8") as f:
            json.dump(movies, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning(f"Не удалось сохранить {MOVIES_FILE}: {e}")


MOVIES: Dict[str, str] = load_movies()

# ======================================
#   ПАМЯТЬ: КОДЫ ПОЛЬЗОВАТЕЛЕЙ + СОСТОЯНИЕ АДМИНА
# ======================================

# user_id -> код (для обычных юзеров, когда просим подписаться)
PENDING_CODES: Dict[int, str] = {}

# простая FSM для админа: user_id -> state
ADMIN_STATES: Dict[int, str] = {}         # например: "add_wait_code", "add_wait_title"
ADMIN_DATA: Dict[int, Dict[str, Any]] = {}  # временные данные, например {"code": "A123"}

# ======================================
#   ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ======================================

async def is_user_subscribed(user_id: int) -> bool:
    """
    Проверка, подписан ли пользователь на все каналы из REQUIRED_CHANNELS.
    Для приватных каналов тут должны быть chat_id (-100...).
    """
    if not REQUIRED_CHANNELS:
        return True

    for channel in REQUIRED_CHANNELS:
        channel_id: Any = channel
        try:
            channel_id = int(channel)
        except ValueError:
            # если это @username, оставляем строкой
            pass

        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        except Exception as e:
            logging.warning(f"Не удалось проверить подписку на {channel}: {e}")
            return False

        if member.status in ("left", "kicked"):
            return False

    return True


def get_channels_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура: перейти в канал + 'Я подписался'.
    """
    kb = InlineKeyboardBuilder()

    kb.row(
        InlineKeyboardButton(
            text="Перейти в канал",
            url=INVITE_LINK,
        )
    )

    kb.row(
        InlineKeyboardButton(
            text="✅ Я подписался",
            callback_data="check_subs",
        )
    )

    return kb.as_markup()


def get_admin_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура для админа.
    """
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="➕ Добавить фильм",
            callback_data="admin_add_movie",
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="📃 Список кодов",
            callback_data="admin_list_movies",
        )
    )
    return kb.as_markup()

# ======================================
#   ОБРАБОТЧИКИ КОМАНД
# ======================================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "Привет! 👋\n\n"
        "Я помогу тебе узнать название фильма по коду из видео.\n"
        "Просто отправь мне код (например: A123)."
    )
    await message.answer(text)


@dp.message(Command("admin"))
async def admin_panel(message: Message):
    """
    /admin — вход в админ-панель.
    """
    if message.from_user.id != ADMIN_ID:
        await message.answer("У тебя нет доступа к админ-панели.")
        return

    await message.answer("Админ-панель:", reply_markup=get_admin_keyboard())


# ======================================
#   ОСНОВНОЙ ОБРАБОТЧИК ТЕКСТА
# ======================================

@dp.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()

    # ----- сначала проверяем, не в режиме ли админ ----- #
    if user_id == ADMIN_ID and user_id in ADMIN_STATES:
        state = ADMIN_STATES[user_id]

        # шаг 1: ждали код фильма
        if state == "add_wait_code":
            code = text.upper()
            ADMIN_DATA[user_id] = {"code": code}
            ADMIN_STATES[user_id] = "add_wait_title"
            await message.answer(f"Код *{code}* сохранён. Теперь отправь название фильма.", parse_mode="Markdown")
            return

        # шаг 2: ждали название фильма
        if state == "add_wait_title":
            title = text
            code = ADMIN_DATA[user_id]["code"]

            MOVIES[code] = title
            save_movies(MOVIES)

            # очищаем состояние
            ADMIN_STATES.pop(user_id, None)
            ADMIN_DATA.pop(user_id, None)

            await message.answer(
                f"✅ Фильм добавлен:\nКод: *{code}*\nНазвание: *{title}*",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard(),
            )
            return

    # ----- если это не админский режим — считаем текст кодом фильма ----- #

    code = text.upper()
    movie_title = MOVIES.get(code)

    if not movie_title:
        await message.answer("❌ Не нашёл такой код. Проверь, правильно ли ты его ввёл.")
        return

    subscribed = await is_user_subscribed(user_id)

    if not subscribed:
        # запоминаем код и просим подписаться
        PENDING_CODES[user_id] = code
        text_msg = (
            "Чтобы узнать название фильма, нужно подписаться на канал 👇\n\n"
            "После подписки нажми кнопку «✅ Я подписался»."
        )
        await message.answer(text_msg, reply_markup=get_channels_keyboard())
        return

    # уже подписан — сразу выдаём фильм
    await message.answer(
        f"🎬 Название фильма по коду *{code}*:\n\n**{movie_title}**",
        parse_mode="Markdown",
    )


# ======================================
#   CALLBACK-ХЭНДЛЕРЫ
# ======================================

@dp.callback_query(F.data == "check_subs")
async def callback_check_subs(callback: CallbackQuery):
    """
    Пользователь нажал 'Я подписался'.
    Если подписка есть и ранее был введён код — сразу выдаём фильм.
    """
    user_id = callback.from_user.id
    subscribed = await is_user_subscribed(user_id)

    if not subscribed:
        await callback.answer("Ты ещё не подписался на канал 🙏", show_alert=True)
        return

    code = PENDING_CODES.get(user_id)

    if not code:
        await callback.message.edit_text(
            "Подписка есть ✅\nТеперь просто отправь код из видео, и я скажу название фильма. 🎬"
        )
        return

    movie_title = MOVIES.get(code)

    if not movie_title:
        await callback.message.edit_text(
            "Ты подписан ✅\nНо что-то пошло не так с кодом. Отправь код ещё раз, пожалуйста."
        )
        PENDING_CODES.pop(user_id, None)
        return

    await callback.message.edit_text(
        f"🎬 Название фильма по коду *{code}*:\n\n**{movie_title}**",
        parse_mode="Markdown",
    )
    PENDING_CODES.pop(user_id, None)


@dp.callback_query(F.data == "admin_add_movie")
async def admin_add_movie(callback: CallbackQuery):
    """
    Кнопка 'Добавить фильм' в админ-панели.
    """
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    ADMIN_STATES[callback.from_user.id] = "add_wait_code"
    ADMIN_DATA.pop(callback.from_user.id, None)

    await callback.message.edit_text(
        "Режим добавления фильма.\n\nОтправь код фильма (например: *A123*).",
        parse_mode="Markdown",
    )


@dp.callback_query(F.data == "admin_list_movies")
async def admin_list_movies(callback: CallbackQuery):
    """
    Показать список кодов и фильмов (первые N).
    """
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    if not MOVIES:
        await callback.message.edit_text("Список фильмов пуст.", reply_markup=get_admin_keyboard())
        return

    # покажем не больше 50, чтобы не раздувать сообщение
    items = list(MOVIES.items())[:50]
    lines = [f"*{code}* — {title}" for code, title in items]
    text = "📃 Список фильмов (первые 50):\n\n" + "\n".join(lines)

    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_admin_keyboard())


# ======================================
#   ЗАПУСК БОТА
# ======================================

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not found in .env")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
