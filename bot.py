import asyncio
import json
import logging
import os
from typing import Dict, Any, Set

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

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")
INVITE_LINK = os.getenv("INVITE_LINK", "")  # пригласительная ссылка на канал

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# если файл у тебя называется иначе (например "фильмы.json") — поменяй здесь
MOVIES_FILE = "movies.json"

# ======================================
#   ЗАГРУЗКА / СОХРАНЕНИЕ ФИЛЬМОВ
# ======================================


def load_movies() -> Dict[str, str]:
    if os.path.exists(MOVIES_FILE):
        try:
            with open(MOVIES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
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
#   ПАМЯТЬ СОСТОЯНИЙ
# ======================================

# user_id -> код (когда ждём подписку)
PENDING_CODES: Dict[int, str] = {}

# FSM админа: user_id -> состояние
ADMIN_STATES: Dict[int, str] = {}
ADMIN_DATA: Dict[int, Dict[str, Any]] = {}

# кто нажал «🚀 Начать»
READY_USERS: Set[int] = set()

# ======================================
#   ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ======================================


async def is_user_subscribed(user_id: int) -> bool:
    """Проверка, подписан ли пользователь на все каналы из REQUIRED_CHANNELS."""
    if not REQUIRED_CHANNELS:
        return True

    for channel in REQUIRED_CHANNELS:
        channel_id: Any = channel
        try:
            channel_id = int(channel)
        except ValueError:
            # если @username — оставляем строкой
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
    """Клавиатура: перейти в канал + 'Я подписался'."""
    kb = InlineKeyboardBuilder()

    if INVITE_LINK:
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
    """Клавиатура для админа."""
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
    kb.row(
        InlineKeyboardButton(
            text="🗑 Удалить фильм",
            callback_data="admin_delete_movie",
        )
    )
    return kb.as_markup()


def get_start_keyboard() -> InlineKeyboardMarkup:
    """Кнопка для активации бота."""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🚀 Начать",
            callback_data="user_start",
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
        "Нажми кнопку «🚀 Начать», чтобы активировать бота,\n"
        "а затем отправь код (например: A123)."
    )
    await message.answer(text, reply_markup=get_start_keyboard())


@dp.message(Command("admin"))
async def admin_panel(message: Message):
    """Вход в админ-панель."""
    if message.from_user.id != ADMIN_ID:
        await message.answer("У тебя нет доступа к админ-панели.")
        return

    await message.answer("Админ-панель:", reply_markup=get_admin_keyboard())

# ======================================
#   CALLBACK-ХЭНДЛЕРЫ
# ======================================


@dp.callback_query(F.data == "user_start")
async def callback_user_start(callback: CallbackQuery):
    """Пользователь нажал «🚀 Начать» — активируем бота для него."""
    user_id = callback.from_user.id
    READY_USERS.add(user_id)

    await callback.message.edit_text(
        "Бот активирован ✅\n\nТеперь просто отправь код из видео, и я скажу название фильма. 🎬"
    )


@dp.callback_query(F.data == "check_subs")
async def callback_check_subs(callback: CallbackQuery):
    """Пользователь нажал 'Я подписался'."""
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
    """Кнопка 'Добавить фильм'."""
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
    """Показать список кодов и фильмов."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    if not MOVIES:
        await callback.message.edit_text(
            "Список фильмов пуст.",
            reply_markup=get_admin_keyboard(),
        )
        return

    items = list(MOVIES.items())[:50]
    lines = [f"*{code}* — {title}" for code, title in items]
    text = "📃 Список фильмов (первые 50):\n\n" + "\n".join(lines)

    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_admin_keyboard(),
    )


@dp.callback_query(F.data == "admin_delete_movie")
async def admin_delete_movie(callback: CallbackQuery):
    """Кнопка 'Удалить фильм'."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    ADMIN_STATES[callback.from_user.id] = "delete_wait_code"
    ADMIN_DATA.pop(callback.from_user.id, None)

    await callback.message.edit_text(
        "Режим удаления фильма.\n\nОтправь код фильма, который нужно удалить (например: *A123*).",
        parse_mode="Markdown",
    )

# ======================================
#   ОБРАБОТЧИК ТЕКСТА
# ======================================


@dp.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()

    # ----- режим админа ----- #
    if user_id == ADMIN_ID and user_id in ADMIN_STATES:
        state = ADMIN_STATES[user_id]

        # добавление фильма: ждём код
        if state == "add_wait_code":
            code = text.upper()
            ADMIN_DATA[user_id] = {"code": code}
            ADMIN_STATES[user_id] = "add_wait_title"
            await message.answer(
                f"Код *{code}* сохранён. Теперь отправь название фильма.",
                parse_mode="Markdown",
            )
            return

        # добавление фильма: ждём название
        if state == "add_wait_title":
            title = text
            code = ADMIN_DATA[user_id]["code"]

            MOVIES[code] = title
            save_movies(MOVIES)

            ADMIN_STATES.pop(user_id, None)
            ADMIN_DATA.pop(user_id, None)

            await message.answer(
                f"✅ Фильм добавлен:\nКод: *{code}*\nНазвание: *{title}*",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard(),
            )
            return

        # удаление фильма
        if state == "delete_wait_code":
            code = text.upper()

            if code in MOVIES:
                title = MOVIES.pop(code)
                save_movies(MOVIES)
                await message.answer(
                    f"🗑 Фильм удалён:\nКод: *{code}*\nНазвание: *{title}*",
                    parse_mode="Markdown",
                    reply_markup=get_admin_keyboard(),
                )
            else:
                await message.answer(
                    f"❌ Фильма с кодом *{code}* нет в базе.",
                    parse_mode="Markdown",
                    reply_markup=get_admin_keyboard(),
                )

            ADMIN_STATES.pop(user_id, None)
            ADMIN_DATA.pop(user_id, None)
            return

    # ----- обычный пользователь ----- #

    # если не нажал «🚀 Начать»
    if user_id not in READY_USERS and user_id != ADMIN_ID:
        await message.answer(
            "Сначала нажми кнопку «🚀 Начать», чтобы активировать бота 😊",
            reply_markup=get_start_keyboard(),
        )
        return

    # считаем текст кодом фильма
    code = text.upper()
    movie_title = MOVIES.get(code)

    if not movie_title:
        await message.answer("❌ Не нашёл такой код. Проверь, правильно ли ты его ввёл.")
        return

    subscribed = await is_user_subscribed(user_id)

    if not subscribed:
        PENDING_CODES[user_id] = code
        text_msg = (
            "Чтобы узнать название фильма, нужно подписаться на канал 👇\n\n"
            "После подписки нажми кнопку «✅ Я подписался»."
        )
        await message.answer(text_msg, reply_markup=get_channels_keyboard())
        return

    await message.answer(
        f"🎬 Название фильма по коду *{code}*:\n\n**{movie_title}**",
        parse_mode="Markdown",
    )

# ======================================
#   ЗАПУСК БОТА
# ======================================


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not found in environment")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
