import os
import requests
import base64
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TG_TOKEN = os.environ.get("TG_BOT_TOKEN")
GH_TOKEN = os.environ.get("GITHUB_TOKEN")
GH_OWNER = os.environ.get("GITHUB_OWNER", "podobowopl-collab")
GH_REPO  = os.environ.get("GITHUB_REPO",  "GRANT-AGENT-COURSE")

if not TG_TOKEN:
    raise ValueError("Ошибка: переменная TG_BOT_TOKEN не задана!")
if not GH_TOKEN:
    raise ValueError("Ошибка: переменная GITHUB_TOKEN не задана!")

LESSONS = {
    "1.1": "M1-Osnovy/urok-01-part1",
    "1.2": "M1-Osnovy/urok-01-part2",
    "1.3": "M1-Osnovy/urok-01-part3",
    "1.4": "M1-Osnovy/urok-02",
    "1.5": "M1-Osnovy/urok-03",
    "2.1": "M2-Instrumenty/urok-04",
    "2.2": "M2-Instrumenty/urok-05",
    "2.3": "M2-Instrumenty/urok-06",
    "2.4": "M2-Instrumenty/urok-07",
    "2.5": "M2-Instrumenty/urok-08",
    "3.1": "M3-Podacha/urok-09",
    "3.2": "M3-Podacha/urok-10",
    "3.3": "M3-Podacha/urok-11",
    "3.4": "M3-Podacha/urok-12",
    "3.5": "M3-Podacha/urok-13",
    "3.6": "M3-Podacha/urok-14",
    "4.1": "M4-Monetizaciya/urok-15",
    "4.2": "M4-Monetizaciya/urok-16",
    "5.1": "M5-Strategii/urok-17",
    "5.2": "M5-Strategii/urok-18",
    "5.3": "M5-Strategii/urok-19",
    "5.4": "M5-Strategii/urok-20",
}

LESSON_NAMES = {
    "1.1": "Профессия грантрайтера",
    "1.2": "Типология заявок",
    "1.3": "Анализ документации",
    "1.4": "Форматы занятости",
    "1.5": "Источники финансирования",
    "2.1": "Платформы и инструменты",
    "2.2": "Шаблоны документации",
    "2.3": "Структура заявки",
    "2.4": "Анализ аудитории",
    "2.5": "Бюджетирование",
    "3.1": "Структура крупных заявок",
    "3.2": "Работа с платформами",
    "3.3": "Коммуникация с донорами",
    "3.4": "Работа с заказчиком",
    "3.5": "Командная работа",
    "3.6": "Правки и нестандартные ситуации",
    "4.1": "Позиционирование",
    "4.2": "Портфолио",
    "5.1": "Мониторинг и отчётность",
    "5.2": "Юридические аспекты",
    "5.3": "Краудфандинг",
    "5.4": "Стратегическое партнёрство",
}

user_state = {}

def gh_headers():
    return {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def upload_to_github(path, content, message):
    url = f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/contents/{path}"
    r = requests.get(url, headers=gh_headers())
    sha = r.json().get("sha") if r.status_code == 200 else None
    encoded = base64.b64encode(content if isinstance(content, bytes) else content.encode()).decode()
    data = {"message": message, "content": encoded}
    if sha:
        data["sha"] = sha
    r = requests.put(url, json=data, headers=gh_headers())
    return r.status_code in [200, 201]

def lesson_keyboard(prefix=""):
    buttons = []
    modules = {"М1": ["1.1","1.2","1.3","1.4","1.5"],
               "М2": ["2.1","2.2","2.3","2.4","2.5"],
               "М3": ["3.1","3.2","3.3","3.4","3.5","3.6"],
               "М4": ["4.1","4.2"],
               "М5": ["5.1","5.2","5.3","5.4"]}
    for mod, lessons in modules.items():
        row = [InlineKeyboardButton(f"{l}", callback_data=f"{prefix}{l}") for l in lessons]
        buttons.append([InlineKeyboardButton(f"── {mod} ──", callback_data="noop")])
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✦ *Grant Architect Bot*\n\n"
        "Привет! Я файловый ассистент курса.\n\n"
        "Что я умею:\n"
        "📄 /upload — загрузить MD файл в урок\n"
        "🔗 /addlink — добавить ссылку в урок\n"
        "📋 /status — статус заполнения курса\n"
        "❓ /help — помощь\n\n"
        "Просто пришли файл или выбери команду!",
        parse_mode="Markdown"
    )

async def upload_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📄 Чтобы загрузить файл — просто пришли его сюда (.md или .pdf).\n"
        "Бот сам спросит, в какой урок загрузить."
    )

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    uid = update.effective_user.id
    fname = doc.file_name or "file"
    
    # save file info to state
    user_state[uid] = {"action": "upload", "file_id": doc.file_id, "fname": fname}
    
    ext = fname.split(".")[-1].lower()
    if ext == "md":
        ftype = "MD (контент урока)"
    elif ext == "pdf":
        ftype = "PDF (материал)"
    else:
        ftype = fname
    
    await update.message.reply_text(
        f"📄 Получен файл: *{fname}* ({ftype})\n\nВыбери урок куда загрузить:",
        parse_mode="Markdown",
        reply_markup=lesson_keyboard("upload_")
    )

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data

    if data == "noop":
        return

    # UPLOAD to lesson
    if data.startswith("upload_"):
        lesson_id = data.replace("upload_", "")
        state = user_state.get(uid, {})
        if not state or state.get("action") != "upload":
            await query.edit_message_text("⚠ Сначала пришли файл!")
            return

        await query.edit_message_text(f"⏳ Загружаю в урок {lesson_id}...")

        file_id = state["file_id"]
        fname = state["fname"]
        lesson_path = LESSONS.get(lesson_id, "")
        if not lesson_path:
            await query.edit_message_text("⚠ Урок не найден!")
            return

        # download file
        tg_file = await ctx.bot.get_file(file_id)
        file_bytes = await tg_file.download_as_bytearray()

        ext = fname.split(".")[-1].lower()
        if ext == "md":
            gh_path = f"{lesson_path}/01-content.md"
            msg = f"📄 Обновлён контент: урок {lesson_id}"
        else:
            gh_path = f"{lesson_path}/materials/{fname}"
            msg = f"📁 Добавлен материал: урок {lesson_id} — {fname}"

        ok = upload_to_github(gh_path, bytes(file_bytes), msg)
        lesson_name = LESSON_NAMES.get(lesson_id, lesson_id)

        if ok:
            await query.edit_message_text(
                f"✅ *Готово!*\n\n"
                f"📌 Урок {lesson_id}: {lesson_name}\n"
                f"📄 Файл: {fname}\n"
                f"📂 Путь: `{gh_path}`\n\n"
                f"Файл загружен на GitHub ✦",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Ошибка загрузки. Проверь токен GitHub.")
        
        user_state.pop(uid, None)

    # ADDLINK to lesson
    elif data.startswith("link_"):
        lesson_id = data.replace("link_", "")
        state = user_state.get(uid, {})
        if not state or state.get("action") != "addlink":
            await query.edit_message_text("⚠ Сначала пришли ссылку командой /addlink")
            return

        lesson_path = LESSONS.get(lesson_id, "")
        link = state.get("link", "")
        desc = state.get("desc", "Материал")
        lesson_name = LESSON_NAMES.get(lesson_id, lesson_id)

        # append to 02-links.md
        gh_path = f"{lesson_path}/02-links.md"
        url = f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/contents/{gh_path}"
        r = requests.get(url, headers=gh_headers())
        
        if r.status_code == 200:
            existing = base64.b64decode(r.json()["content"]).decode()
            sha = r.json()["sha"]
        else:
            existing = f"# Ссылки — Урок {lesson_id}: {lesson_name}\n\n"
            sha = None

        new_content = existing + f"- [{desc}]({link})\n"
        encoded = base64.b64encode(new_content.encode()).decode()
        payload = {"message": f"🔗 Добавлена ссылка: урок {lesson_id}", "content": encoded}
        if sha:
            payload["sha"] = sha
        
        r2 = requests.put(url, json=payload, headers=gh_headers())
        ok = r2.status_code in [200, 201]

        if ok:
            await query.edit_message_text(
                f"✅ *Ссылка добавлена!*\n\n"
                f"📌 Урок {lesson_id}: {lesson_name}\n"
                f"🔗 {desc}\n`{link}`",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Ошибка. Проверь токен GitHub.")
        
        user_state.pop(uid, None)

async def addlink_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 1:
        await update.message.reply_text(
            "Используй так:\n`/addlink https://... Описание ссылки`",
            parse_mode="Markdown"
        )
        return
    link = args[0]
    desc = " ".join(args[1:]) if len(args) > 1 else "Материал"
    uid = update.effective_user.id
    user_state[uid] = {"action": "addlink", "link": link, "desc": desc}
    await update.message.reply_text(
        f"🔗 Ссылка: {link}\n📝 Описание: {desc}\n\nВыбери урок:",
        reply_markup=lesson_keyboard("link_")
    )

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Проверяю GitHub...")
    filled = []
    empty = []
    for lid, path in LESSONS.items():
        url = f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/contents/{path}/01-content.md"
        r = requests.get(url, headers=gh_headers())
        if r.status_code == 200:
            size = r.json().get("size", 0)
            if size > 100:
                filled.append(f"✅ {lid} — {LESSON_NAMES[lid]}")
            else:
                empty.append(f"⬜ {lid} — {LESSON_NAMES[lid]}")
        else:
            empty.append(f"❌ {lid} — {LESSON_NAMES[lid]}")
    
    msg = "📊 *Статус курса Grant Architect*\n\n"
    if filled:
        msg += f"*Заполнено ({len(filled)}/22):*\n" + "\n".join(filled) + "\n\n"
    if empty:
        msg += f"*Пустые ({len(empty)}/22):*\n" + "\n".join(empty)
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✦ *Grant Architect Bot — Помощь*\n\n"
        "*Загрузка файлов:*\n"
        "Просто пришли .md или .pdf файл → выбери урок\n\n"
        "*Добавить ссылку:*\n"
        "`/addlink https://example.com Название`\n\n"
        "*Статус курса:*\n"
        "`/status` — покажет какие уроки заполнены\n\n"
        "*Форматы файлов:*\n"
        "• .md → идёт в 01-content.md урока\n"
        "• .pdf → идёт в папку materials/\n"
        "• ссылка → идёт в 02-links.md урока",
        parse_mode="Markdown"
    )

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.startswith("http"):
        uid = update.effective_user.id
        user_state[uid] = {"action": "addlink", "link": text, "desc": "Материал"}
        await update.message.reply_text(
            f"🔗 Ссылка получена!\nВыбери урок:",
            reply_markup=lesson_keyboard("link_")
        )
    else:
        await update.message.reply_text(
            "Пришли файл (.md или .pdf) или ссылку.\nИли выбери команду: /upload /addlink /status"
        )

def main():
    app = Application.builder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("upload", upload_cmd))
    app.add_handler(CommandHandler("addlink", addlink_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))
    print("✦ Grant Architect Bot запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
