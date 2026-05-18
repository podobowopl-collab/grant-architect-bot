import asyncio
import base64
import json
import logging
import os
import signal
from datetime import datetime

import requests
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
    "audits",
]

COMMAND_FOLDERS = {
    "grant":     ("grants",    ["eu", "usa", "poland", "startups"]),
    "project":   ("projects",  ["ideas", "applications"]),
    "knowledge": ("knowledge", ["guides", "templates"]),
    "upload":    ("uploads",   None),
}

ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt", "md"}

user_state: dict = {}

# ---------------------------------------------------------------------------
# Audit conversation states
# ---------------------------------------------------------------------------
(
    A_NAME, A_BUSINESS, A_PRODUCT, A_AUDIENCE, A_OFFER,
    A_TRAFFIC, A_SALES_PROCESS,
    A_AI_LEVEL, A_PAINS, A_MAIN_PAIN,
    A_PROCESS_STEPS, A_CHANNELS, A_OUTPUT,
    A_POINT_B,
    A_IMPORTANCE, A_READINESS,
) = range(16)

AUDIT_QUESTIONS = {
    A_NAME: (
        "👤 *Блок 1 из 5 — Вводный контекст*\n\n"
        "Представьтесь, пожалуйста: кто вы и откуда?"
    ),
    A_BUSINESS: "Чем вы занимаетесь? Какой у вас бизнес или проект?",
    A_PRODUCT: "Что вы предлагаете или продаёте? Основные продукты, услуги или направления?",
    A_AUDIENCE: "Кто ваша целевая аудитория? Кому вы продаёте или хотите продавать?",
    A_OFFER: (
        "Как сейчас для вашей целевой аудитории звучит ваш оффер?\n"
        "Какую главную проблему клиента вы решаете?"
    ),
    A_TRAFFIC: (
        "Откуда сейчас приходят клиенты?\n"
        "Платный трафик, соцсети, сарафанное радио, партнёры, сайт, мессенджеры, другое?"
    ),
    A_SALES_PROCESS: (
        "Как сейчас происходит процесс продаж в 3–5 шагах?\n"
        "Откуда приходит клиент → где начинается контакт → кто отвечает → что дальше?"
    ),
    A_AI_LEVEL: (
        "🤖 *Блок 2 из 5 — Точка А: AI и боли*\n\n"
        "Как бы вы оценили свой уровень осознанности и опыта в нейронках от 1 до 10?\n"
        "_(1 — только начинаю, 10 — AI работает во всех возможных процессах)_"
    ),
    A_PAINS: (
        "Какие сейчас самые главные боли в бизнесе, работе или ежедневных задачах?\n"
        "Что больше всего мешает двигаться быстрее или зарабатывать больше?"
    ),
    A_MAIN_PAIN: (
        "Если выбрать *одну* самую сильную боль — что это будет?\n"
        "Что больше всего мешает, надоело, утомляет или забирает энергию?\n"
        "Как эта боль звучит простыми словами?"
    ),
    A_PROCESS_STEPS: (
        "⚙️ *Блок 3 из 5 — Конкретизация боли*\n\n"
        "Как сейчас этот процесс происходит по шагам?\n"
        "Где начинается → что происходит дальше → кто участвует → где главный затык?"
    ),
    A_CHANNELS: (
        "Через какие каналы и инструменты сейчас проходит этот процесс?\n"
        "Instagram, Telegram, WhatsApp, сайт, CRM, почта, звонки, Google Sheets, Excel — что-то другое?"
    ),
    A_OUTPUT: (
        "Что должно получиться на выходе после автоматизации, чтобы вы сказали «да, это работает»?\n"
        "Заявка, запись, отчёт, КП, сообщение менеджеру, таблица, контент, дашборд — что именно?"
    ),
    A_POINT_B: (
        "🎯 *Блок 4 из 5 — Точка Б*\n\n"
        "Какой результат решения этой боли вы будете считать положительным?\n"
        "К чему вы хотите прийти? Опишите вашу точку Б."
    ),
    A_IMPORTANCE: (
        "💪 *Блок 5 из 5 — Мотивация и готовность*\n\n"
        "Насколько вам важно получить этот результат?\n"
        "Это «неплохо было бы сделать» или уже накипело и нужно решить как можно скорее?"
    ),
    A_READINESS: (
        "Насколько вы готовы приложить усилия, чтобы это реализовать — от 1 до 10?\n"
        "_(1 — посмотрю что будет, 10 — готов действовать прямо сейчас)_"
    ),
}

AUDIT_KEYS = [
    ("Кто вы", A_NAME),
    ("Бизнес/проект", A_BUSINESS),
    ("Продукт/услуга", A_PRODUCT),
    ("Целевая аудитория", A_AUDIENCE),
    ("Оффер и проблема клиента", A_OFFER),
    ("Источники трафика", A_TRAFFIC),
    ("Процесс продаж", A_SALES_PROCESS),
    ("Уровень AI (1–10)", A_AI_LEVEL),
    ("Основные боли", A_PAINS),
    ("Главная боль", A_MAIN_PAIN),
    ("Процесс по шагам", A_PROCESS_STEPS),
    ("Каналы и инструменты", A_CHANNELS),
    ("Желаемый результат автоматизации", A_OUTPUT),
    ("Точка Б", A_POINT_B),
    ("Важность результата", A_IMPORTANCE),
    ("Готовность действовать (1–10)", A_READINESS),
]

NEXT_STATE = {
    A_NAME: A_BUSINESS,
    A_BUSINESS: A_PRODUCT,
    A_PRODUCT: A_AUDIENCE,
    A_AUDIENCE: A_OFFER,
    A_OFFER: A_TRAFFIC,
    A_TRAFFIC: A_SALES_PROCESS,
    A_SALES_PROCESS: A_AI_LEVEL,
    A_AI_LEVEL: A_PAINS,
    A_PAINS: A_MAIN_PAIN,
    A_MAIN_PAIN: A_PROCESS_STEPS,
    A_PROCESS_STEPS: A_CHANNELS,
    A_CHANNELS: A_OUTPUT,
    A_OUTPUT: A_POINT_B,
    A_POINT_B: A_IMPORTANCE,
    A_IMPORTANCE: A_READINESS,
}

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


def build_audit_markdown(answers: dict, user_info: str, ts: str) -> str:
    lines = [
        f"# Аудит бизнеса и AI-внедрения",
        f"",
        f"**Пользователь:** {user_info}",
        f"**Дата:** {ts}",
        f"",
        "---",
        "",
    ]
    for label, state_key in AUDIT_KEYS:
        answer = answers.get(state_key, "—")
        lines.append(f"## {label}")
        lines.append("")
        lines.append(answer)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Audit conversation handlers
# ---------------------------------------------------------------------------
async def audit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["audit"] = {}
    await update.message.reply_text(
        "🚀 *Аудит бизнеса и задач для AI-внедрения*\n\n"
        "Точка А → Точка Б\n\n"
        "Это займёт 10–15 минут. Я задам 16 вопросов в 5 блоках, "
        "чтобы найти лучшую точку входа для AI в ваш бизнес.\n\n"
        "Напишите /cancel в любой момент, чтобы прервать аудит.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + AUDIT_QUESTIONS[A_NAME],
        parse_mode="Markdown",
    )
    return A_NAME


async def _save_and_next(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    current_state: int,
) -> int:
    ctx.user_data["audit"][current_state] = update.message.text.strip()
    next_state = NEXT_STATE.get(current_state)
    if next_state is None:
        return await audit_finish(update, ctx)
    question = AUDIT_QUESTIONS[next_state]
    await update.message.reply_text(question, parse_mode="Markdown")
    return next_state


async def audit_q_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_NAME)

async def audit_q_business(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_BUSINESS)

async def audit_q_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_PRODUCT)

async def audit_q_audience(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_AUDIENCE)

async def audit_q_offer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_OFFER)

async def audit_q_traffic(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_TRAFFIC)

async def audit_q_sales(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_SALES_PROCESS)

async def audit_q_ai_level(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_AI_LEVEL)

async def audit_q_pains(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_PAINS)

async def audit_q_main_pain(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_MAIN_PAIN)

async def audit_q_steps(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_PROCESS_STEPS)

async def audit_q_channels(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_CHANNELS)

async def audit_q_output(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_OUTPUT)

async def audit_q_point_b(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_POINT_B)

async def audit_q_importance(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _save_and_next(update, ctx, A_IMPORTANCE)


async def audit_finish(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    answers = ctx.user_data.get("audit", {})
    answers[A_READINESS] = update.message.text.strip()

    user = update.effective_user
    user_info = f"{user.full_name} (@{user.username or 'no_username'}, id:{user.id})"
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    await update.message.reply_text("⏳ Сохраняю результаты аудита...", parse_mode="Markdown")

    md_content = build_audit_markdown(answers, user_info, ts)
    safe_name = (user.username or str(user.id)).replace("/", "_")
    date_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"audit_{safe_name}_{date_str}.md"
    gh_path = f"audits/{filename}"

    ok, _ = gh_upload_file(gh_path, md_content.encode("utf-8"), f"audit: {filename}")

    # Build readable summary for Telegram
    summary_lines = [
        "✅ *Аудит завершён!*\n",
        f"📄 Результаты сохранены в `{gh_path}`\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "*📋 Краткое резюме:*\n",
    ]
    for label, state_key in AUDIT_KEYS:
        val = answers.get(state_key, "—")
        short = val[:120] + "…" if len(val) > 120 else val
        summary_lines.append(f"*{label}:*\n{short}\n")

    summary_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    summary_lines.append(
        "\n🔜 Следующий шаг: на основе этого аудита можно подобрать "
        "конкретные AI-инструменты и автоматизации для вашего бизнеса."
    )

    summary = "\n".join(summary_lines)

    # Telegram messages are limited to 4096 chars; split if needed
    chunks = [summary[i:i+4000] for i in range(0, len(summary), 4000)]
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="Markdown")

    if not ok:
        await update.message.reply_text(
            "⚠️ Не удалось сохранить файл в GitHub, но резюме выше сохраните вручную.",
            parse_mode="Markdown",
        )

    ctx.user_data.pop("audit", None)
    return ConversationHandler.END


async def audit_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("audit", None)
    await update.message.reply_text(
        "❌ Аудит прерван. Чтобы начать заново, отправьте /audit.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# File upload handlers
# ---------------------------------------------------------------------------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✦ *Grant Architect Bot*\n\n"
        "Я храню файлы для курса, помогаю искать гранты и провожу AI-аудит бизнеса.\n\n"
        "🔍 *Аудит:*\n"
        "/audit — пройти аудит бизнеса и задач для AI-внедрения\n\n"
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


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✦ *Помощь — Grant Architect Bot*\n\n"
        "*AI-аудит бизнеса:*\n"
        "`/audit` — запускает пошаговый аудит (16 вопросов, 5 блоков)\n"
        "Результаты сохраняются в `/audits/` на GitHub\n\n"
        "*Как загрузить файл:*\n"
        "1. Отправь команду (/grant, /project, /knowledge, /upload)\n"
        "2. Выбери подпапку (кнопки появятся)\n"
        "3. Пришли файл (PDF, DOCX, DOC, TXT, MD)\n"
        "4. Файл автоматически сохранится в GitHub\n\n"
        "*Поиск:*\n"
        "`/search грант` — ищет по имени файлов в репозитории\n\n"
        "*Структура папок в GitHub:*\n"
        "```\n"
        "audits/\n"
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

    audit_handler = ConversationHandler(
        entry_points=[CommandHandler("audit", audit_start)],
        states={
            A_NAME:          [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_name)],
            A_BUSINESS:      [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_business)],
            A_PRODUCT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_product)],
            A_AUDIENCE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_audience)],
            A_OFFER:         [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_offer)],
            A_TRAFFIC:       [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_traffic)],
            A_SALES_PROCESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_sales)],
            A_AI_LEVEL:      [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_ai_level)],
            A_PAINS:         [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_pains)],
            A_MAIN_PAIN:     [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_main_pain)],
            A_PROCESS_STEPS: [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_steps)],
            A_CHANNELS:      [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_channels)],
            A_OUTPUT:        [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_output)],
            A_POINT_B:       [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_point_b)],
            A_IMPORTANCE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q_importance)],
            A_READINESS:     [MessageHandler(filters.TEXT & ~filters.COMMAND, audit_finish)],
        },
        fallbacks=[CommandHandler("cancel", audit_cancel)],
        allow_reentry=True,
    )

    tg_app.add_handler(audit_handler)
    tg_app.add_handler(CommandHandler("start",     start))
    tg_app.add_handler(CommandHandler("help",      help_cmd))
    tg_app.add_handler(CommandHandler("grant",     grant_cmd))
    tg_app.add_handler(CommandHandler("project",   project_cmd))
    tg_app.add_handler(CommandHandler("knowledge", knowledge_cmd))
    tg_app.add_handler(CommandHandler("upload",    upload_cmd))
    tg_app.add_handler(CommandHandler("search",    search_cmd))
    tg_app.add_handler(CallbackQueryHandler(subfolder_callback, pattern=r"^sf_"))
    tg_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    web_app = web.Application()
    web_app.router.add_get("/",          api_health)
    web_app.router.add_get("/health",    api_health)
    web_app.router.add_get("/api/files", api_files)

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
