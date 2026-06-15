import asyncio
import base64
import json
import logging
import os
import signal

import requests
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

TG_TOKEN      = os.environ.get("TG_BOT_TOKEN")
GH_TOKEN      = os.environ.get("GITHUB_TOKEN")
GH_OWNER      = os.environ.get("GITHUB_OWNER", "podobowopl-collab")
GH_REPO       = os.environ.get("GITHUB_REPO",  "GRANT-AGENT-COURSE")
PORT          = int(os.environ.get("PORT", 8080))
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))   # ваш Telegram user_id
PAYMENT_LINK  = os.environ.get("PAYMENT_LINK", "")          # ссылка на оплату
TMA_URL       = os.environ.get("TMA_URL", "https://tma-diagnostic-prototype.vercel.app")

if not TG_TOKEN:
    raise ValueError("TG_BOT_TOKEN is not set")
if not GH_TOKEN:
    raise ValueError("GITHUB_TOKEN is not set")

GH_BASE = f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/contents"

# ---------------------------------------------------------------------------
# Folder structure to create on first run
# ---------------------------------------------------------------------------
INIT_FOLDERS = [
    "grants/eu",
    "grants/usa",
    "grants/poland",
    "grants/startups",
    "projects/ideas",
    "projects/applications",
    "knowledge/guides",
    "knowledge/templates",
    "uploads",
]

# command → (root_folder, subfolders_or_None)
COMMAND_FOLDERS = {
    "grant":     ("grants",    ["eu", "usa", "poland", "startups"]),
    "project":   ("projects",  ["ideas", "applications"]),
    "knowledge": ("knowledge", ["guides", "templates"]),
    "upload":    ("uploads",   None),
}

ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt", "md"}

# in-memory session state  {user_id: {"folder": str, "ready": bool}}
user_state: dict = {}

# booking state  {booking_id: {client_chat_id, name, contact, date, time_slot}}
pending_bookings: dict = {}


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------
def gh_headers() -> dict:
    return {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def gh_upload_file(path: str, content_bytes: bytes, message: str) -> tuple[bool, dict]:
    """Create or update a file in GitHub. Returns (ok, response_json)."""
    url = f"{GH_BASE}/{path}"
    r = requests.get(url, headers=gh_headers(), timeout=15)
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload: dict = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode(),
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(url, json=payload, headers=gh_headers(), timeout=30)
    return r.status_code in (200, 201), r.json()


def gh_list_files(folder: str = "", depth: int = 0) -> list[dict]:
    """Recursively list all non-.gitkeep files under *folder*."""
    if depth > 5:
        return []
    url = f"{GH_BASE}/{folder}" if folder else GH_BASE
    r = requests.get(url, headers=gh_headers(), timeout=15)
    if r.status_code != 200:
        return []
    items = r.json()
    if not isinstance(items, list):
        return []

    result = []
    for item in items:
        if item["type"] == "file" and item["name"] != ".gitkeep":
            result.append({
                "name": item["name"],
                "path": item["path"],
                "size": item.get("size", 0),
                "download_url": item.get("download_url"),
            })
        elif item["type"] == "dir":
            result.extend(gh_list_files(item["path"], depth + 1))
    return result


def ensure_folder_structure() -> None:
    """Create .gitkeep placeholders for every required folder."""
    for folder in INIT_FOLDERS:
        path = f"{folder}/.gitkeep"
        url = f"{GH_BASE}/{path}"
        r = requests.get(url, headers=gh_headers(), timeout=10)
        if r.status_code == 404:
            payload = {
                "message": f"chore: init folder {folder}",
                "content": base64.b64encode(b"").decode(),
            }
            r2 = requests.put(url, json=payload, headers=gh_headers(), timeout=15)
            if r2.status_code in (200, 201):
                logger.info("Created GitHub folder: %s", folder)
            else:
                logger.warning("Could not create folder %s: %s", folder, r2.status_code)


# ---------------------------------------------------------------------------
# Telegram command handlers
# ---------------------------------------------------------------------------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    # Deep-link from booking page: /start bk_<encoded>
    if ctx.args and ctx.args[0].startswith("bk_"):
        await _handle_booking(update, ctx, ctx.args[0][3:])
        return

    await update.message.reply_text(
        "✦ *Grant Architect Bot*\n\n"
        "Я храню файлы для курса и помогаю искать гранты.\n\n"
        "📁 *Загрузка файлов:*\n"
        "/grant — сохранить в /grants\n"
        "/project — сохранить в /projects\n"
        "/knowledge — сохранить в /knowledge\n"
        "/upload — сохранить в /uploads\n\n"
        "🔍 *Поиск:*\n"
        "/search слово — поиск файлов по имени\n\n"
        "Форматы: PDF, DOCX, TXT, MD",
        parse_mode="Markdown",
    )


async def _handle_booking(update: Update, ctx: ContextTypes.DEFAULT_TYPE, encoded: str) -> None:
    """Called when a client opens the bot via booking deep-link."""
    import time as _time

    uid = update.effective_user.id

    # Decode compact booking data (base64url → UTF-8 pipe-separated fields)
    try:
        padded = encoded.replace("-", "+").replace("_", "/")
        padded += "=" * (4 - len(padded) % 4)
        decoded = base64.b64decode(padded).decode("utf-8")
        parts   = decoded.split("|")
        name    = parts[0] if len(parts) > 0 else update.effective_user.first_name or "Клиент"
        date    = parts[1] if len(parts) > 1 else "—"
        time_s  = parts[2] if len(parts) > 2 else "—"
        contact = parts[3] if len(parts) > 3 else "—"
    except Exception:
        name = update.effective_user.first_name or "Клиент"
        date = time_s = contact = "—"

    # Store booking
    booking_id = f"{uid}_{int(_time.time())}"
    pending_bookings[booking_id] = {
        "client_chat_id": uid,
        "name":     name,
        "contact":  contact,
        "date":     date,
        "time_slot": time_s,
    }

    # Confirm to client
    await update.message.reply_text(
        f"✅ *Запись подтверждена!*\n\n"
        f"🗓 {date}  🕐 {time_s}\n\n"
        "После нашей сессии вы получите здесь ссылку на оплату.\n\n"
        "_Больше ничего делать не нужно — ждём вас!_",
        parse_mode="Markdown",
    )

    # Notify admin
    if ADMIN_CHAT_ID:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ Отправить ссылку на оплату",
                callback_data=f"pay_{booking_id}",
            )
        ]])
        text = (
            f"📅 *Новая запись на консультацию*\n\n"
            f"👤 {name}\n"
            f"📞 {contact}\n"
            f"🗓 {date}  🕐 {time_s}"
        )
        await ctx.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=text,
            parse_mode="Markdown",
            reply_markup=kb,
        )
    else:
        logger.warning("ADMIN_CHAT_ID not set — booking notification skipped")


async def payment_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin clicks ✅ → bot sends payment link to client."""
    query = update.callback_query
    booking_id = query.data[4:]  # strip "pay_"
    booking = pending_bookings.get(booking_id)

    if not booking:
        await query.answer("❌ Запись не найдена или уже обработана.", show_alert=True)
        return

    from datetime import datetime, timedelta
    deadline = (datetime.now() + timedelta(hours=24)).strftime("%-d %B в %H:%M")
    link = PAYMENT_LINK or "—"

    try:
        await ctx.bot.send_message(
            chat_id=booking["client_chat_id"],
            text=(
                f"Привет, {booking['name']}! 👋\n\n"
                f"Спасибо за нашу сессию!\n\n"
                f"Ссылка для оплаты консультации:\n"
                f"🔗 {link}\n\n"
                f"⏰ Оплатите, пожалуйста, до *{deadline}*"
            ),
            parse_mode="Markdown",
        )
        await query.answer("✅ Ссылка отправлена!")
        await query.edit_message_text(
            query.message.text + "\n\n✅ *Ссылка на оплату отправлена!*",
            parse_mode="Markdown",
        )
        pending_bookings.pop(booking_id, None)

    except Exception as exc:
        logger.exception("payment_callback error")
        await query.answer(f"❌ Ошибка: {exc}", show_alert=True)


async def tunnel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Open the Lia Strategy TMA (Body of Tunnel) as a Web App."""
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🚀 Открыть диагностику",
            web_app={"url": TMA_URL},
        )
    ]])
    await update.message.reply_text(
        "✦ *Тело туннеля*\n\n"
        "Нажмите кнопку ниже, чтобы пройти диагностику и записаться на созвон.",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✦ *Помощь — Grant Architect Bot*\n\n"
        "*Как загрузить файл:*\n"
        "1. Отправь команду (/grant, /project, /knowledge, /upload)\n"
        "2. Выбери подпапку (кнопки появятся)\n"
        "3. Пришли файл (PDF, DOCX, DOC, TXT, MD)\n"
        "4. Файл автоматически сохранится в GitHub\n\n"
        "*Поиск:*\n"
        "`/search грант` — ищет по имени файлов в репозитории\n\n"
        "*Структура папок в GitHub:*\n"
        "```\n"
        "grants/  eu/ usa/ poland/ startups/\n"
        "projects/  ideas/ applications/\n"
        "knowledge/  guides/ templates/\n"
        "uploads/\n"
        "```\n"
        "*API для приложения:*\n"
        "`GET /api/files` — список всех файлов",
        parse_mode="Markdown",
    )


async def _set_upload_mode(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    mode: str,
) -> None:
    uid = update.effective_user.id
    root_folder, subfolders = COMMAND_FOLDERS[mode]

    if subfolders:
        buttons: list[list] = []
        row: list = []
        for sf in subfolders:
            row.append(
                InlineKeyboardButton(sf, callback_data=f"sf_{root_folder}/{sf}")
            )
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([
            InlineKeyboardButton(
                f"📂 Корень ({root_folder}/)",
                callback_data=f"sf_{root_folder}",
            )
        ])
        user_state[uid] = {"mode": mode, "folder": root_folder, "ready": False}
        await update.message.reply_text(
            f"📁 Выбери подпапку для *{root_folder}/*:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    else:
        user_state[uid] = {"mode": mode, "folder": root_folder, "ready": True}
        await update.message.reply_text(
            f"📤 Готов! Пришли файл — сохраню в *{root_folder}/*\n"
            "Форматы: PDF, DOCX, TXT, MD",
            parse_mode="Markdown",
        )


async def grant_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_upload_mode(update, ctx, "grant")


async def project_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_upload_mode(update, ctx, "project")


async def knowledge_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_upload_mode(update, ctx, "knowledge")


async def upload_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_upload_mode(update, ctx, "upload")


async def subfolder_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    # data = "sf_grants/eu" or "sf_grants"
    folder_path = query.data[3:]  # strip "sf_"
    state = user_state.get(uid, {})
    state["folder"] = folder_path
    state["ready"] = True
    user_state[uid] = state
    await query.edit_message_text(
        f"✅ Папка выбрана: *{folder_path}/*\n\nТеперь пришли файл (PDF, DOCX, TXT, MD).",
        parse_mode="Markdown",
    )


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    state = user_state.get(uid, {})

    if not state.get("ready"):
        await update.message.reply_text(
            "⚠ Сначала выбери тип файла:\n/grant /project /knowledge /upload"
        )
        return

    doc = update.message.document
    fname = doc.file_name or "file"
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""

    if ext not in ALLOWED_EXTENSIONS:
        await update.message.reply_text(
            f"⚠ Формат .{ext} не поддерживается.\n"
            f"Поддерживаются: {', '.join(sorted(ALLOWED_EXTENSIONS)).upper()}"
        )
        return

    status_msg = await update.message.reply_text(
        f"⏳ Скачиваю *{fname}*...", parse_mode="Markdown"
    )

    # Download from Telegram
    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception as exc:
        await status_msg.edit_text(f"❌ Ошибка скачивания из Telegram:\n`{exc}`", parse_mode="Markdown")
        return

    await status_msg.edit_text(f"⏳ Загружаю в GitHub...", parse_mode="Markdown")

    folder = state["folder"]
    gh_path = f"{folder}/{fname}"
    commit_msg = f"upload: {gh_path} via Telegram bot"

    ok, resp = gh_upload_file(gh_path, file_bytes, commit_msg)

    if ok:
        size_kb = len(file_bytes) / 1024
        logger.info("Uploaded to GitHub: %s", gh_path)
        await status_msg.edit_text(
            f"✅ *Загружено в GitHub!*\n\n"
            f"📄 Файл: `{fname}`\n"
            f"📂 Путь: `{gh_path}`\n"
            f"📦 Размер: {size_kb:.1f} KB\n\n"
            f"✦ Uploaded to GitHub: {gh_path}",
            parse_mode="Markdown",
        )
        user_state.pop(uid, None)
    else:
        err = resp.get("message", "Unknown error")
        await status_msg.edit_text(
            f"❌ Ошибка загрузки в GitHub:\n`{err}`\n\n"
            "Проверь GITHUB_TOKEN и права на репозиторий.",
            parse_mode="Markdown",
        )


async def search_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text(
            "Использование: `/search keyword`", parse_mode="Markdown"
        )
        return

    keyword = " ".join(ctx.args).lower()
    await update.message.reply_text(
        f"🔍 Ищу файлы с `{keyword}`...", parse_mode="Markdown"
    )

    all_files = gh_list_files()
    matches = [
        f for f in all_files
        if keyword in f["name"].lower() or keyword in f["path"].lower()
    ]

    if not matches:
        await update.message.reply_text(
            f"❌ Файлы по запросу *{keyword}* не найдены.", parse_mode="Markdown"
        )
        return

    lines = [f"✅ Найдено: {len(matches)} файл(ов) по запросу *{keyword}*\n"]
    for f in matches[:20]:
        size_kb = f["size"] / 1024
        lines.append(f"📄 `{f['path']}` ({size_kb:.1f} KB)")
    if len(matches) > 20:
        lines.append(f"\n…и ещё {len(matches) - 20} файлов")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# aiohttp web server  (/api/files, /health)
# ---------------------------------------------------------------------------
async def api_files(request: web.Request) -> web.Response:
    folder = request.rel_url.query.get("folder", "")
    try:
        files = gh_list_files(folder)
        return web.json_response({
            "ok": True,
            "count": len(files),
            "repo": f"{GH_OWNER}/{GH_REPO}",
            "files": files,
        })
    except Exception as exc:
        logger.exception("api_files error")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def api_health(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "service": "grant-architect-bot",
        "repo": f"{GH_OWNER}/{GH_REPO}",
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    # 1. Initialise GitHub folder structure (blocking, but runs once at startup)
    logger.info("Initialising GitHub folder structure…")
    try:
        ensure_folder_structure()
    except Exception as exc:
        logger.warning("Could not init folder structure: %s", exc)

    # 2. Build Telegram application
    tg_app = Application.builder().token(TG_TOKEN).build()
    tg_app.add_handler(CommandHandler("start",     start))
    tg_app.add_handler(CommandHandler("tunnel",    tunnel_cmd))
    tg_app.add_handler(CommandHandler("help",      help_cmd))
    tg_app.add_handler(CommandHandler("grant",     grant_cmd))
    tg_app.add_handler(CommandHandler("project",   project_cmd))
    tg_app.add_handler(CommandHandler("knowledge", knowledge_cmd))
    tg_app.add_handler(CommandHandler("upload",    upload_cmd))
    tg_app.add_handler(CommandHandler("search",    search_cmd))
    tg_app.add_handler(CallbackQueryHandler(subfolder_callback, pattern=r"^sf_"))
    tg_app.add_handler(CallbackQueryHandler(payment_callback,   pattern=r"^pay_"))
    tg_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # 3. Build aiohttp web app
    web_app = web.Application()
    web_app.router.add_get("/",          api_health)
    web_app.router.add_get("/health",    api_health)
    web_app.router.add_get("/api/files", api_files)

    # 4. Run Telegram polling + HTTP server concurrently
    async with tg_app:
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        logger.info("✦ Grant Architect Bot запущен!")

        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        logger.info("✦ Web server on port %d", PORT)

        # Block until SIGINT / SIGTERM
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()

        logger.info("Shutting down…")
        await tg_app.updater.stop()
        await tg_app.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
