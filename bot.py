import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, List, Tuple
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.storage.memory import MemoryStorage
# ===== КОНФИГУРАЦИЯ =====
APP_ROOT = Path(__file__).resolve().parent
INFO_DIR_NAME = "Информация"
INFO_ROOT = APP_ROOT / INFO_DIR_NAME

# ===== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =====
subscribers: set[int] = set()  # Подписчики на уведомления
known_files: set[str] = set()  # Известные файлы для отслеживания
user_progress: Dict[int, Dict[str, bool]] = {}  # Прогресс изучения пользователей
user_content_messages: Dict[int, List[int]] = {}  # ID сообщений с контентом для очистки

# ===== УПРОЩЕННЫЕ ЭМОДЗИ =====
def get_emoji(name: str) -> str:
    """Получить эмодзи для раздела или контента"""
    name_lower = name.lower()
    if any(word in name_lower for word in ["базовый", "введение", "что такое"]):
        return "🟢"
    elif any(word in name_lower for word in ["средний", "атака", "человек"]):
        return "🟡"
    elif any(word in name_lower for word in ["продвинутый", "фишинг", "взлом"]):
        return "🔴"
    elif any(word in name_lower for word in ["команды", "система", "оборудование", "пользователь"]):
        return "🔵"
    elif any(word in name_lower for word in ["введение", "что такое"]):
        return "👋"
    elif any(word in name_lower for word in ["команды", "система"]):
        return "⚡"
    elif any(word in name_lower for word in ["атака", "фишинг"]):
        return "🎯"
    else:
        return "📁" if "dir" in name_lower else "📘"


class PathRegistry:
    """Регистрирует относительные пути и выдаёт короткие id для callback_data."""

    def __init__(self) -> None:
        self._id_to_path: Dict[str, Tuple[str, str]] = {}
        self._path_to_id: Dict[Tuple[str, str], str] = {}
        self._counter: int = 0

    def get_id(self, kind: str, rel_path: str) -> str:
        key = (kind, rel_path)
        if key in self._path_to_id:
            return self._path_to_id[key]
        self._counter += 1
        assigned_id = str(self._counter)
        self._path_to_id[key] = assigned_id
        self._id_to_path[assigned_id] = (kind, rel_path)
        return assigned_id

    def resolve(self, assigned_id: str) -> Tuple[str, str]:
        return self._id_to_path[assigned_id]




# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====

def ensure_info_root() -> None:
    """Проверяет существование папки с информацией"""
    if not INFO_ROOT.exists() or not INFO_ROOT.is_dir():
        raise FileNotFoundError(
            f"Папка '{INFO_DIR_NAME}' не найдена рядом с файлом: {APP_ROOT}"
        )


def list_dir(rel_dir: str) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """Получает список папок и .txt файлов в указанной директории"""
    base = (INFO_ROOT / rel_dir).resolve()
    if INFO_ROOT not in base.parents and base != INFO_ROOT:
        raise PermissionError("Выход за пределы корневой папки Информация запрещён")

    dir_items: List[Tuple[str, str]] = []
    file_items: List[Tuple[str, str]] = []
    for entry in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.is_dir():
            dir_items.append((entry.name, str((Path(rel_dir) / entry.name).as_posix())))
        else:
            if entry.suffix.lower() == ".txt":
                file_items.append((entry.stem, str((Path(rel_dir) / entry.name).as_posix())))
    return dir_items, file_items


def read_text_file(rel_path: str) -> str:
    """Читает содержимое .txt файла"""
    file_path = (INFO_ROOT / rel_path).resolve()
    if INFO_ROOT not in file_path.parents and file_path != INFO_ROOT:
        raise PermissionError("Выход за пределы корневой папки Информация запрещён")
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()








async def clear_user_messages(bot: Bot, chat_id: int) -> None:
    """Удаляет предыдущие сообщения с контентом при переходе"""
    ids = user_content_messages.get(chat_id) or []
    if not ids:
        return
    for mid in ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass
    user_content_messages[chat_id] = []



def build_dir_keyboard(rel_dir: str, user_id: int = 0) -> InlineKeyboardMarkup:
    dirs, files = list_dir(rel_dir)
    rows: List[List[InlineKeyboardButton]] = []

    # Директории с цветовой индикацией
    for display_name, child_rel in dirs:
        iid = path_registry.get_id("dir", child_rel)
        emoji = get_emoji(display_name)
        # Проверяем прогресс изучения
        progress = ""
        if user_id and user_id in user_progress:
            studied_count = sum(1 for f in files if user_progress[user_id].get(f[1], False))
            total_count = len(files)
            if total_count > 0:
                progress = f" ({studied_count}/{total_count})"
        
        rows.append([InlineKeyboardButton(
            text=f"{emoji} {display_name}{progress}", 
            callback_data=f"open_dir:{iid}"
        )])

    # Файлы с индикацией изучения
    for display_name, child_rel in files:
        iid = path_registry.get_id("file", child_rel)
        emoji = get_emoji(display_name)
        # Индикатор изучения
        studied = "✅" if user_id and user_progress.get(user_id, {}).get(child_rel, False) else "📖"
        rows.append([InlineKeyboardButton(
            text=f"{studied} {emoji} {display_name}", 
            callback_data=f"open_file:{iid}"
        )])

    # Навигация
    nav_row: List[InlineKeyboardButton] = []
    if rel_dir:
        parent = str(Path(rel_dir).parent.as_posix()) if Path(rel_dir).parent.as_posix() != "." else ""
        pid = path_registry.get_id("dir", parent)
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"open_dir:{pid}"))
    home_id = path_registry.get_id("dir", "")
    nav_row.append(InlineKeyboardButton(text="🏠 Домой", callback_data=f"open_dir:{home_id}"))
    rows.append(nav_row)

    # Дополнительные кнопки
    action_row = []
    cur_id = path_registry.get_id("dir", rel_dir)
    action_row.append(InlineKeyboardButton(text="🔄 Обновить", callback_data=f"open_dir:{cur_id}"))
    action_row.append(InlineKeyboardButton(text="🔍 Поиск", callback_data="search"))
    action_row.append(InlineKeyboardButton(text="📊 Статистика", callback_data="stats"))
    rows.append(action_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_home_keyboard(user_id: int = 0) -> InlineKeyboardMarkup:
    home_id = path_registry.get_id("dir", "")
    
    # Статистика для главной страницы
    stats_text = ""
    if user_id and user_id in user_progress:
        total_files = len(scan_all_txt())
        studied_files = sum(1 for f in user_progress[user_id].values() if f)
        if total_files > 0:
            percentage = (studied_files / total_files) * 100
            stats_text = f"\n📊 Прогресс: {studied_files}/{total_files} ({percentage:.1f}%)"
    
    buttons = [
        [InlineKeyboardButton(text="🚀 Приступить к изучению Kali Linux", callback_data=f"open_dir:{home_id}")],
        [InlineKeyboardButton(text="🔍 Поиск по материалам", callback_data="search")],
        [InlineKeyboardButton(text="📊 Моя статистика", callback_data="stats")],


    ]
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ===== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =====
path_registry = PathRegistry()

# ===== ОСНОВНОЙ РОУТЕР =====
router = Router()

# ===== ОБРАБОТЧИКИ КОМАНД =====

@router.message(CommandStart())
async def on_start(message: Message) -> None:
    ensure_info_root()
    subscribers.add(message.chat.id)
    
    # Инициализируем прогресс пользователя
    if message.from_user.id not in user_progress:
        user_progress[message.from_user.id] = {}
    
    greeting = (
        "🎯 <b>Добро пожаловать в Kali Linux Academy!</b>\n\n"
        "🔥 <i>Ваш персональный гид по кибербезопасности</i>\n\n"
        "📚 <b>Что вас ждёт:</b>\n"
        "🟢 <b>Базовый уровень</b> — основы и введение\n"
        "🟡 <b>Средний уровень</b> — практические атаки\n"
        "🔴 <b>Продвинутый уровень</b> — экспертные техники\n\n"
        "✨ <b>Особенности:</b>\n"
        "• Автоматические уведомления о новых материалах\n"
        "• Отслеживание прогресса изучения\n"
        "• Поиск по всем материалам\n"
        "• Красивое оформление с цветовой индикацией\n\n"
        "🚀 <b>Начните изучение прямо сейчас!</b>"
    )
    await message.answer(greeting, reply_markup=build_home_keyboard(message.from_user.id))

@router.message(Command("stop"))
async def on_stop(message: Message) -> None:
    if message.chat.id in subscribers:
        subscribers.discard(message.chat.id)
    await message.answer("Вы отписались от уведомлений о новых материалах.")


@router.message(Command("home"))
async def on_home(message: Message) -> None:
    await on_start(message)

@router.message(Command("search"))
async def on_search_command(message: Message) -> None:
    await message.answer(
        "🔍 <b>Поиск по материалам</b>\n\n"
        "Отправьте ключевое слово для поиска по всем файлам.\n"
        "Например: <code>команды</code>, <code>атака</code>, <code>безопасность</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главная", callback_data="home")]
        ])
    )


@router.message(Command("stats"))
async def on_stats_command(message: Message) -> None:
    user_id = message.from_user.id
    if user_id not in user_progress:
        user_progress[user_id] = {}
    
    total_files = len(scan_all_txt())
    studied_files = sum(1 for f in user_progress[user_id].values() if f)
    
    if total_files == 0:
        await message.answer("📊 Пока нет материалов для изучения.")
        return
    
    percentage = (studied_files / total_files) * 100
    progress_bar = "🟩" * int(percentage / 10) + "⬜" * (10 - int(percentage / 10))
    
    stats_text = (
        f"📊 <b>Ваша статистика изучения</b>\n\n"
        f"📚 Изучено материалов: <b>{studied_files}/{total_files}</b>\n"
        f"📈 Прогресс: <b>{percentage:.1f}%</b>\n\n"
        f"<code>{progress_bar}</code>\n\n"
    )
    
    if studied_files == total_files:
        stats_text += "🎉 <b>Поздравляем! Вы изучили все материалы!</b>"
    elif studied_files > 0:
        remaining = total_files - studied_files
        stats_text += f"💪 Осталось изучить: <b>{remaining}</b> материалов"
    else:
        stats_text += "🚀 Начните изучение с базовых разделов!"
    
    await message.answer(stats_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главная", callback_data="home")]
    ]))


# ===== ОБРАБОТЧИКИ CALLBACK =====

@router.callback_query(F.data.startswith("open_dir:"))
async def on_open_dir(callback: CallbackQuery) -> None:
    try:
        assigned_id = callback.data.split(":", 1)[1]
        kind, rel_path = path_registry.resolve(assigned_id)
        if kind != "dir":
            await callback.answer("Это не папка", show_alert=False)
            return
        
        user_id = callback.from_user.id
        kb = build_dir_keyboard(rel_path, user_id)
        
        # Красивый заголовок с эмодзи
        if rel_path:
            section_name = Path(rel_path).name
            emoji = get_emoji(section_name)
            title = f"{emoji} {section_name}\nРаздел: {rel_path}\n———"
        else:
            title = f"🎯 Kali Linux Academy\nГлавное меню\n———"
        
        # Очищаем ранее отправленные контент-сообщения
        await clear_user_messages(callback.message.bot, callback.message.chat.id)
        
        # Проверяем, изменился ли контент перед редактированием
        try:
            await callback.message.edit_text(title, reply_markup=kb)
        except Exception as edit_error:
            if "message is not modified" in str(edit_error):
                # Сообщение не изменилось, просто отвечаем
                await callback.answer()
                return
            else:
                raise edit_error
        await callback.answer()
    except KeyError:
        await callback.answer("Ссылка устарела — откройте заново", show_alert=True)
    except Exception as e:
        logging.exception("Ошибка при открытии папки: %s", e)
        await callback.answer("Ошибка при открытии", show_alert=True)




@router.callback_query(F.data.startswith("open_file:"))
async def on_open_file(callback: CallbackQuery) -> None:
    try:
        assigned_id = callback.data.split(":", 1)[1]
        kind, rel_path = path_registry.resolve(assigned_id)
        if kind != "file":
            await callback.answer("Это не файл", show_alert=False)
            return
        
        # Отмечаем файл как изученный
        user_id = callback.from_user.id
        if user_id not in user_progress:
            user_progress[user_id] = {}
        user_progress[user_id][rel_path] = True
        
        text = read_text_file(rel_path)
        parent_dir = str(Path(rel_path).parent.as_posix()) if Path(rel_path).parent.as_posix() != "." else ""
        kb = build_dir_keyboard(parent_dir, user_id)
        
        # Красивый заголовок с эмодзи
        file_name = Path(rel_path).name
        emoji = get_emoji(file_name)
        header = f"✅ {emoji} {file_name}\nРаздел: {str(Path(rel_path).parent.as_posix() or '/')}\n———"

        # Сразу отвечаем на callback, чтобы избежать timeout
        await callback.answer("✅ Материал отмечен как изученный!")

        # Очищаем предыдущие контент-сообщения
        await clear_user_messages(callback.message.bot, callback.message.chat.id)

        # Обновляем сообщение заголовком и клавиатурой (HTML)
        await callback.message.edit_text(header, reply_markup=kb)

        # Отправляем содержимое .txt файла как простой текст
        if len(text) > 4000:
            # Разбиваем длинный текст на части
            parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
        else:
            parts = [text]
        
        sent_ids: List[int] = []
        for part in parts:
            try:
                msg = await callback.message.answer(part)
                sent_ids.append(msg.message_id)
            except Exception as e:
                logging.warning(f"Ошибка отправки текста: {e}")
        
        # Сохраняем ID отправленных сообщений для последующей очистки
        if sent_ids:
            user_content_messages[callback.message.chat.id] = sent_ids
    except KeyError:
        try:
            await callback.answer("Ссылка устарела — откройте заново", show_alert=True)
        except:
            pass  # Игнорируем ошибки callback
    except Exception as e:
        logging.exception("Ошибка при открытии файла: %s", e)
        try:
            await callback.answer("Ошибка при открытии", show_alert=True)
        except:
            pass


# ===== ОБРАБОТЧИКИ СПЕЦИАЛЬНЫХ КНОПОК =====

@router.callback_query(F.data == "search")
async def on_search_callback(callback: CallbackQuery) -> None:
    await clear_user_messages(callback.message.bot, callback.message.chat.id)
    await callback.message.edit_text(
        "🔍 <b>Поиск по материалам</b>\n\n"
        "Отправьте ключевое слово для поиска по всем файлам.\n"
        "Например: <code>команды</code>, <code>атака</code>, <code>безопасность</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главная", callback_data="home")]
        ]),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@router.callback_query(F.data == "stats")
async def on_stats_callback(callback: CallbackQuery) -> None:
    await clear_user_messages(callback.message.bot, callback.message.chat.id)
    user_id = callback.from_user.id
    if user_id not in user_progress:
        user_progress[user_id] = {}
    
    total_files = len(scan_all_txt())
    studied_files = sum(1 for f in user_progress[user_id].values() if f)
    
    if total_files == 0:
        await callback.message.edit_text("📊 Пока нет материалов для изучения.")
        return
    
    percentage = (studied_files / total_files) * 100
    progress_bar = "🟩" * int(percentage / 10) + "⬜" * (10 - int(percentage / 10))
    
    stats_text = (
        f"📊 <b>Ваша статистика изучения</b>\n\n"
        f"📚 Изучено материалов: <b>{studied_files}/{total_files}</b>\n"
        f"📈 Прогресс: <b>{percentage:.1f}%</b>\n\n"
        f"<code>{progress_bar}</code>\n\n"
    )
    
    if studied_files == total_files:
        stats_text += "🎉 <b>Поздравляем! Вы изучили все материалы!</b>"
    elif studied_files > 0:
        remaining = total_files - studied_files
        stats_text += f"💪 Осталось изучить: <b>{remaining}</b> материалов"
    else:
        stats_text += "🚀 Начните изучение с базовых разделов!"
    
    await callback.message.edit_text(stats_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главная", callback_data="home")]
    ]), parse_mode=ParseMode.HTML)
    await callback.answer()



@router.callback_query(F.data == "home")
async def on_home_callback(callback: CallbackQuery) -> None:
    await clear_user_messages(callback.message.bot, callback.message.chat.id)
    user_id = callback.from_user.id
    if user_id not in user_progress:
        user_progress[user_id] = {}
    
    greeting = (
        "🎯 <b>Добро пожаловать в Kali Linux Academy!</b>\n\n"
        "🔥 <i>Ваш персональный гид по кибербезопасности</i>\n\n"
        "📚 <b>Что вас ждёт:</b>\n"
        "🟢 <b>Базовый уровень</b> — основы и введение\n"
        "🟡 <b>Средний уровень</b> — практические атаки\n"
        "🔴 <b>Продвинутый уровень</b> — экспертные техники\n\n"
        "✨ <b>Особенности:</b>\n"
        "• Автоматические уведомления о новых материалах\n"
        "• Отслеживание прогресса изучения\n"
        "• Поиск по всем материалам\n"
        "• Красивое оформление с цветовой индикацией\n\n"
        "🚀 <b>Начните изучение прямо сейчас!</b>"
    )
    await callback.message.edit_text(greeting, reply_markup=build_home_keyboard(user_id), parse_mode=ParseMode.HTML)
    await callback.answer()


# ===== ПОИСК ПО ТЕКСТУ =====

@router.message()
async def on_text_search(message: Message) -> None:
    if not message.text or message.text.startswith('/'):
        return
    
    search_term = message.text.lower().strip()
    if len(search_term) < 2:
        await message.answer("🔍 Введите минимум 2 символа для поиска.")
        return
    
    results = []
    for file_path in scan_all_txt():
        try:
            content = read_text_file(file_path).lower()
            if search_term in content or search_term in file_path.lower():
                # Находим контекст вокруг найденного слова
                lines = content.split('\n')
                matching_lines = []
                for i, line in enumerate(lines):
                    if search_term in line:
                        start = max(0, i-1)
                        end = min(len(lines), i+2)
                        context = '\n'.join(lines[start:end])
                        matching_lines.append(context[:200] + "..." if len(context) > 200 else context)
                        if len(matching_lines) >= 3:  # Ограничиваем количество результатов
                            break
                
                if matching_lines:
                    results.append((file_path, matching_lines))
        except Exception as e:
            logging.warning(f"Ошибка при поиске в файле {file_path}: {e}")
    
    if not results:
        await message.answer(
            f"🔍 По запросу '{search_term}' ничего не найдено.\n\n"
            "Попробуйте другие ключевые слова или проверьте правописание.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Главная", callback_data="home")]
            ]),
        )
        return
    
    # Отправляем результаты поиска
    response = f"🔍 Результаты поиска по запросу: '{search_term}'\n\n"
    
    for i, (file_path, contexts) in enumerate(results[:5]):  # Ограничиваем 5 результатами
        file_name = Path(file_path).name
        emoji = get_emoji(file_name)
        response += f"{i+1}. {emoji} {file_name}\n"
        response += f"Путь: {file_path}\n"
        
        # Добавляем кнопку для открытия файла
        file_id = path_registry.get_id("file", file_path)
        
        for context in contexts[:2]:  # Показываем максимум 2 контекста
            response += f"{context}\n"
        
        response += "\n"
    
    if len(results) > 5:
        response += f"... и ещё {len(results) - 5} результатов\n\n"
    
    # Кнопки для навигации
    buttons = []
    for i, (file_path, _) in enumerate(results[:3]):  # Кнопки для первых 3 результатов
        file_name = Path(file_path).name
        file_id = path_registry.get_id("file", file_path)
        buttons.append([InlineKeyboardButton(text=f"📖 {file_name}", callback_data=f"open_file:{file_id}")])
    
    buttons.append([InlineKeyboardButton(text="🏠 Главная", callback_data="home")])
    
    await message.answer(response, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)


# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ МОНИТОРИНГА =====

def scan_all_txt() -> set[str]:
    """Сканирует все .txt файлы в папке Информация"""
    files: set[str] = set()
    for path in INFO_ROOT.rglob("*.txt"):
        rel = path.relative_to(INFO_ROOT).as_posix()
        files.add(rel)
    return files


async def watch_info_changes(bot: Bot, interval_seconds: int = 10) -> None:
    """Фоновый мониторинг папки на предмет новых файлов"""
    global known_files
    known_files = scan_all_txt()
    while True:
        try:
            current = scan_all_txt()
            new_files = current - known_files
            if new_files:
                for rel in sorted(new_files):
                    parent_dir = str(Path(rel).parent.as_posix()) if Path(rel).parent.as_posix() != "." else ""
                    file_id = path_registry.get_id("file", rel)
                    dir_id = path_registry.get_id("dir", parent_dir)
                    kb = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="📘 Открыть файл", callback_data=f"open_file:{file_id}")],
                            [InlineKeyboardButton(text="📂 Открыть раздел", callback_data=f"open_dir:{dir_id}")],
                        ]
                    )
                    for chat_id in list(subscribers):
                        try:
                            await bot.send_message(chat_id, f"🆕 Добавлен новый материал: {rel}", reply_markup=kb)
                        except Exception as e:
                            logging.warning("Не удалось отправить уведомление %s: %s", chat_id, e)
                known_files = current
        except Exception as e:
            logging.warning("Ошибка в наблюдателе папки: %s", e)
        await asyncio.sleep(interval_seconds)


# ===== ГЛАВНАЯ ФУНКЦИЯ ЗАПУСКА =====

async def main() -> None:
    """Основная функция запуска бота"""
    logging.basicConfig(level=logging.INFO)
    ensure_info_root()

    # Загрузка токена из .env
    env_path = APP_ROOT / ".env"
    load_dotenv(dotenv_path=env_path, override=False)
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    
    if not token:
        raise RuntimeError("Не задан токен. Укажите TELEGRAM_BOT_TOKEN или BOT_TOKEN в .env")
    
    # Валидация токена
    token = token.strip()
    if "PASTE_YOUR_TOKEN_HERE" in token or token == "":
        raise RuntimeError("В .env оставлен плейсхолдер токена. Вставьте реальный токен от @BotFather")

    # Создание бота и диспетчера
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    async with Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) as bot:
        # Проверка токена
        try:
            await bot.get_me()
        except Exception as e:
            logging.error("Проверка токена не пройдена: %r", e)
            raise RuntimeError(f"Ошибка при проверке токена: {str(e)}")

        # Запуск фонового мониторинга и polling
        asyncio.create_task(watch_info_changes(bot))
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


# ===== ТОЧКА ВХОДА =====

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
