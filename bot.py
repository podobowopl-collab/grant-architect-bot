import asyncio
import base64
import json
import logging
import os
import pathlib
import signal

import requests
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
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

TG_TOKEN = os.environ.get("TG_BOT_TOKEN")
GH_TOKEN = os.environ.get("GITHUB_TOKEN")
GH_OWNER = os.environ.get("GITHUB_OWNER", "podobowopl-collab")
GH_REPO  = os.environ.get("GITHUB_REPO",  "GRANT-AGENT-COURSE")
PORT     = int(os.environ.get("PORT", 8080))

if not TG_TOKEN:
    raise ValueError("TG_BOT_TOKEN is not set")
if not GH_TOKEN:
    raise ValueError("GITHUB_TOKEN is not set")

GH_BASE = f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/contents"

# URL where the Mini App is served (set in Render env vars after first deploy)
MINI_APP_URL = os.environ.get("MINI_APP_URL", "")

# ---------------------------------------------------------------------------
# Folder structure
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

COMMAND_FOLDERS = {
    "grant":     ("grants",    ["eu", "usa", "poland", "startups"]),
    "project":   ("projects",  ["ideas", "applications"]),
    "knowledge": ("knowledge", ["guides", "templates"]),
    "upload":    ("uploads",   None),
}

ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt", "md"}

# in-memory upload state  {user_id: {"folder": str, "ready": bool}}
user_state: dict = {}

# ---------------------------------------------------------------------------
# Grant application wizard states
# ---------------------------------------------------------------------------
W_NAME, W_DESC, W_LOCATION, W_MODEL, W_BUDGET, W_GRANT_TYPE = range(6)


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------
def gh_headers() -> dict:
    return {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def gh_upload_file(path: str, content_bytes: bytes, message: str) -> tuple[bool, dict]:
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
# File upload handlers
# ---------------------------------------------------------------------------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    buttons: list[list] = []
    if MINI_APP_URL:
        buttons.append([
            InlineKeyboardButton(
                "🚀 Открыть Grant Architect",
                web_app=WebAppInfo(url=MINI_APP_URL),
            )
        ])
    buttons.append([InlineKeyboardButton("📋 Wizard заявки (в чате)", callback_data="start_apply")])

    await update.message.reply_text(
        "✦ *Grant Architect Bot*\n\n"
        "Помогаю собрать заявку на грант и хранить файлы курса.\n\n"
        "📋 *Заявка на грант:*\n"
        "/apply — пройти wizard и получить черновик заявки\n\n"
        "📁 *Загрузка файлов:*\n"
        "/grant /project /knowledge /upload\n\n"
        "🔍 /search слово — поиск файлов",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✦ *Помощь — Grant Architect Bot*\n\n"
        "*Wizard заявки:*\n"
        "/apply — 6 вопросов о проекте → черновик заявки + сохранение в GitHub\n\n"
        "*Как загрузить файл:*\n"
        "1. Отправь команду (/grant, /project и т.д.)\n"
        "2. Выбери подпапку\n"
        "3. Пришли файл (PDF, DOCX, DOC, TXT, MD)\n\n"
        "*Поиск:*\n"
        "`/search грант` — ищет по именам файлов\n\n"
        "*Структура папок:*\n"
        "```\n"
        "grants/  eu/ usa/ poland/ startups/\n"
        "projects/  ideas/ applications/\n"
        "knowledge/  guides/ templates/\n"
        "uploads/\n"
        "```",
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

    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception as exc:
        await status_msg.edit_text(
            f"❌ Ошибка скачивания из Telegram:\n`{exc}`", parse_mode="Markdown"
        )
        return

    await status_msg.edit_text("⏳ Загружаю в GitHub...", parse_mode="Markdown")

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
            f"📦 Размер: {size_kb:.1f} KB",
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
# Grant application wizard
# ---------------------------------------------------------------------------
async def apply_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text(
        "📋 *Wizard заявки на грант*\n\n"
        "Я задам 6 вопросов и составлю черновик заявки.\n"
        "Напиши /cancel в любой момент чтобы выйти.\n\n"
        "*Шаг 1 из 6*\n"
        "Как называется твой проект?",
        parse_mode="Markdown",
    )
    return W_NAME


async def w_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["name"] = update.message.text
    await update.message.reply_text(
        "*Шаг 2 из 6*\n"
        "Опиши проект: что он делает и какую проблему решает?",
        parse_mode="Markdown",
    )
    return W_DESC


async def w_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["desc"] = update.message.text
    await update.message.reply_text(
        "*Шаг 3 из 6*\n"
        "В какой стране / регионе работает проект?",
        parse_mode="Markdown",
    )
    return W_LOCATION


async def w_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["location"] = update.message.text
    await update.message.reply_text(
        "*Шаг 4 из 6*\n"
        "Как устроен бизнес: модель, команда, стадия?\n"
        "_(идея / MVP / работающий продукт)_",
        parse_mode="Markdown",
    )
    return W_MODEL


async def w_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["model"] = update.message.text
    await update.message.reply_text(
        "*Шаг 5 из 6*\n"
        "Какой бюджет нужен и есть ли собственный вклад?",
        parse_mode="Markdown",
    )
    return W_BUDGET


async def w_budget(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["budget"] = update.message.text
    buttons = [
        [
            InlineKeyboardButton("🇪🇺 EU", callback_data="gtype_eu"),
            InlineKeyboardButton("🇺🇸 USA", callback_data="gtype_usa"),
        ],
        [
            InlineKeyboardButton("🇵🇱 Польша", callback_data="gtype_poland"),
            InlineKeyboardButton("🚀 Стартапы", callback_data="gtype_startups"),
        ],
    ]
    await update.message.reply_text(
        "*Шаг 6 из 6*\n"
        "Какой тип гранта тебя интересует?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return W_GRANT_TYPE


async def w_grant_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    grant_type_map = {
        "gtype_eu": "EU",
        "gtype_usa": "USA",
        "gtype_poland": "Польша",
        "gtype_startups": "Стартапы",
    }
    grant_type = grant_type_map.get(query.data, query.data)
    ctx.user_data["grant_type"] = grant_type

    d = ctx.user_data
    project_name = d.get("name", "Проект")

    draft = (
        f"# Черновик заявки на грант\n\n"
        f"## Проект: {project_name}\n\n"
        f"**Описание:**\n{d.get('desc', '')}\n\n"
        f"**Местонахождение:**\n{d.get('location', '')}\n\n"
        f"**Бизнес-модель и стадия:**\n{d.get('model', '')}\n\n"
        f"**Бюджет:**\n{d.get('budget', '')}\n\n"
        f"**Тип гранта:** {grant_type}\n\n"
        f"---\n\n"
        f"## Питч (1 абзац)\n\n"
        f"{project_name} решает проблему [опиши боль рынка] "
        f"через {d.get('desc', '[опиши решение]')}. "
        f"Команда: {d.get('model', '[команда и стадия]')}. "
        f"Мы ищем финансирование {d.get('budget', '[сумма]')} "
        f"для масштабирования в регионе {d.get('location', '[регион]')}.\n\n"
        f"---\n\n"
        f"## Структура презентации для комиссии\n\n"
        f"1. **Проблема:** [опиши боль рынка]\n"
        f"2. **Решение:** {d.get('desc', '')}\n"
        f"3. **Рынок:** [размер и потенциал]\n"
        f"4. **Команда:** {d.get('model', '')}\n"
        f"5. **Финансы:** {d.get('budget', '')}\n"
        f"6. **Следующие шаги:** [что сделаешь с грантом]\n\n"
        f"---\n"
        f"*Сгенерировано Grant Architect Bot*\n"
    )

    await query.edit_message_text(
        "⏳ Составляю черновик и сохраняю в GitHub...",
        parse_mode="Markdown",
    )

    safe_name = project_name.lower().replace(" ", "_")[:40]
    gh_path = f"projects/applications/{safe_name}_application.md"
    ok, _ = gh_upload_file(gh_path, draft.encode("utf-8"), f"apply: {project_name}")

    saved_note = f"\n\n📂 Сохранено: `{gh_path}`" if ok else ""

    # Telegram has 4096 char limit; send draft in chunks if needed
    header = f"✅ *Черновик заявки готов!*{saved_note}\n\n"
    full_text = header + draft
    if len(full_text) <= 4096:
        await ctx.bot.send_message(
            query.from_user.id, full_text, parse_mode="Markdown"
        )
    else:
        await ctx.bot.send_message(
            query.from_user.id, header, parse_mode="Markdown"
        )
        # Send draft in 4000-char chunks
        for i in range(0, len(draft), 4000):
            await ctx.bot.send_message(
                query.from_user.id,
                f"```\n{draft[i:i+4000]}\n```",
                parse_mode="Markdown",
            )

    ctx.user_data.clear()
    return ConversationHandler.END


async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text("❌ Wizard отменён. Напиши /apply чтобы начать заново.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# aiohttp web server
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


async def serve_miniapp(request: web.Request) -> web.Response:
    html_path = pathlib.Path(__file__).parent / "index.html"
    if not html_path.exists():
        return web.Response(status=404, text="Mini App not found")
    return web.Response(
        body=html_path.read_bytes(),
        content_type="text/html",
        charset="utf-8",
    )


# ---------------------------------------------------------------------------
# Mini App data handler (receives form data sent by Telegram.WebApp.sendData)
# ---------------------------------------------------------------------------
async def handle_web_app_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.message.web_app_data.data
    try:
        data = json.loads(raw)
    except Exception:
        await update.message.reply_text("❌ Ошибка обработки данных из Mini App.")
        return

    project_name = data.get("name") or "Проект"
    grant_name   = data.get("grant_name", "")
    grant_type   = data.get("grant_type", "eu")
    region       = data.get("region", "")
    problem      = data.get("problem", "")
    audience     = data.get("audience", "")
    budget       = data.get("budget", "")
    team         = data.get("team", "")

    draft = (
        f"# Черновик заявки на грант\n\n"
        f"## Проект: {project_name}\n\n"
        f"**Грант:** {grant_name}\n"
        f"**Регион:** {region}\n\n"
        f"**Проблема:**\n{problem}\n\n"
        f"**Целевая аудитория:** {audience}\n\n"
        f"**Бюджет:** {budget}\n"
        f"**Команда:** {team}\n\n"
        f"---\n\n"
        f"## Питч\n\n"
        f"{project_name} решает [{problem[:80] if problem else 'проблему'}] "
        f"для аудитории '{audience}'. "
        f"Команда: {team}. Бюджет: {budget}.\n\n"
        f"---\n\n"
        f"## Структура для комиссии\n\n"
        f"1. **Проблема:** {problem}\n"
        f"2. **Решение:** [опиши]\n"
        f"3. **Рынок:** {region}\n"
        f"4. **Команда:** {team}\n"
        f"5. **Финансы:** {budget}\n"
        f"6. **Следующие шаги:** [что сделаешь с грантом]\n\n"
        f"---\n"
        f"*Сгенерировано Grant Architect Mini App*\n"
    )

    safe_name = project_name.lower().replace(" ", "_")[:40]
    gh_path = f"projects/applications/{safe_name}_application.md"
    ok, _ = gh_upload_file(gh_path, draft.encode("utf-8"), f"apply: {project_name} via Mini App")

    saved_note = f"\n📂 Сохранено: `{gh_path}`" if ok else ""
    header = f"✅ *Черновик заявки готов!*{saved_note}\n\n"

    full = header + draft
    if len(full) <= 4096:
        await update.message.reply_text(full, parse_mode="Markdown")
    else:
        await update.message.reply_text(header, parse_mode="Markdown")
        for i in range(0, len(draft), 4000):
            await update.message.reply_text(
                f"```\n{draft[i:i+4000]}\n```", parse_mode="Markdown"
            )


async def start_apply_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    ctx.user_data.clear()
    await query.message.reply_text(
        "📋 *Wizard заявки на грант*\n\n"
        "Я задам 6 вопросов и составлю черновик заявки.\n"
        "Напиши /cancel чтобы выйти.\n\n"
        "*Шаг 1 из 6*\nКак называется твой проект?",
        parse_mode="Markdown",
    )
    ctx.user_data["_wizard_started"] = True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    logger.info("Initialising GitHub folder structure…")
    try:
        ensure_folder_structure()
    except Exception as exc:
        logger.warning("Could not init folder structure: %s", exc)

    tg_app = Application.builder().token(TG_TOKEN).build()

    # Grant application wizard (ConversationHandler must be registered first)
    grant_wizard = ConversationHandler(
        entry_points=[CommandHandler("apply", apply_cmd)],
        states={
            W_NAME:       [MessageHandler(filters.TEXT & ~filters.COMMAND, w_name)],
            W_DESC:       [MessageHandler(filters.TEXT & ~filters.COMMAND, w_desc)],
            W_LOCATION:   [MessageHandler(filters.TEXT & ~filters.COMMAND, w_location)],
            W_MODEL:      [MessageHandler(filters.TEXT & ~filters.COMMAND, w_model)],
            W_BUDGET:     [MessageHandler(filters.TEXT & ~filters.COMMAND, w_budget)],
            W_GRANT_TYPE: [CallbackQueryHandler(w_grant_type, pattern=r"^gtype_")],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    tg_app.add_handler(grant_wizard)

    # File upload commands
    tg_app.add_handler(CommandHandler("start",     start))
    tg_app.add_handler(CommandHandler("help",      help_cmd))
    tg_app.add_handler(CommandHandler("grant",     grant_cmd))
    tg_app.add_handler(CommandHandler("project",   project_cmd))
    tg_app.add_handler(CommandHandler("knowledge", knowledge_cmd))
    tg_app.add_handler(CommandHandler("upload",    upload_cmd))
    tg_app.add_handler(CommandHandler("search",    search_cmd))
    tg_app.add_handler(CallbackQueryHandler(subfolder_callback, pattern=r"^sf_"))
    tg_app.add_handler(CallbackQueryHandler(start_apply_callback, pattern=r"^start_apply$"))
    tg_app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    tg_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    web_app = web.Application()
    web_app.router.add_get("/",          api_health)
    web_app.router.add_get("/health",    api_health)
    web_app.router.add_get("/api/files", api_files)
    web_app.router.add_get("/app",       serve_miniapp)

    async with tg_app:
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        logger.info("✦ Grant Architect Bot запущен!")

        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        logger.info("✦ Web server on port %d", PORT)

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
