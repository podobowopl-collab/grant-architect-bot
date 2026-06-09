"""
Liya AI Bot — продающая воронка до стратегической сессии.
Idea → Money OS / Meaning Tunnel

Воронка:
  Кодовое слово → видео → квалификация → оффер → запись → подтверждение

Env vars:
  LIYA_BOT_TOKEN    — токен бота (@BotFather → /newbot)
  CODE_WORD        — кодовое слово для входа (по умолчанию: AI)
  SALES_VIDEO_URL  — ссылка на продающее видео (Loom / YouTube)
  CALENDAR_LINK    — ссылка на Google Calendar / Calendly для записи
  ADMIN_CHAT_ID    — твой личный Telegram chat_id (получить через @userinfobot)
  PORT             — порт (по умолчанию: 8080)
"""
import asyncio
import logging
import os
import signal

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
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TG_TOKEN      = os.environ.get("LIYA_BOT_TOKEN")
CODE_WORD     = os.environ.get("CODE_WORD",       "AI").upper()
VIDEO_URL     = os.environ.get("SALES_VIDEO_URL", "")
CALENDAR_LINK = os.environ.get("CALENDAR_LINK",   "")
ADMIN_ID      = os.environ.get("ADMIN_CHAT_ID",   "")
PORT          = int(os.environ.get("PORT", 8080))

if not TG_TOKEN:
    raise ValueError("LIYA_BOT_TOKEN is not set")

# ---------------------------------------------------------------------------
# Funnel states
# ---------------------------------------------------------------------------
F_QUALIFY, F_BOOK, F_DATE = range(3)

# ---------------------------------------------------------------------------
# Texts (edit here to change the tone/copy)
# ---------------------------------------------------------------------------
MSG_WELCOME = (
    "Привет! Я Liya AI 👋\n\n"
    "Я помогаю предпринимателям строить систему *идея → деньги* с помощью AI.\n\n"
    "Без команды. Без хаоса. Без бесконечных инструментов.\n"
    "Один чёткий путь — от идеи до продажи.\n\n"
)

MSG_VIDEO = (
    "Сначала посмотри короткое видео — там я объясняю как это работает:\n\n"
    "{video_url}\n\n"
    "Посмотрела? Тогда расскажи коротко:\n"
    "*Чем занимаешься и где сейчас главный затык — что не двигается?*"
)

MSG_VIDEO_NO_URL = (
    "Расскажи коротко:\n"
    "*Чем занимаешься и где сейчас главный затык — что не двигается?*"
)

MSG_EMPATHY = (
    "Понимаю. Это именно то, с чем приходят ко мне.\n\n"
    "Когда нет системы — всё держится на твоей энергии. "
    "Ты знаешь что делать, но не знаешь *в какую точку вложить AI*, "
    "чтобы это дало реальный сдвиг.\n\n"
    "Именно это я разбираю на стратегической сессии:\n"
    "находим *твою первую точку* — и ты выходишь с конкретным планом.\n\n"
)

MSG_OFFER = (
    "Хочешь разберём твою ситуацию?\n\n"
    "Это *бесплатная* стратегическая сессия 45 минут — "
    "я смотрю на твой бизнес и говорю честно, где AI даёт результат первее всего."
)

MSG_BOOK_YES = (
    "Отлично! 🙌\n\n"
    "Вот мой календарь — выбери удобное время:\n"
    "{calendar_link}\n\n"
    "После выбора времени напиши мне сюда подтверждение — "
    "и я пришлю всё необходимое перед сессией."
)

MSG_BOOK_YES_NO_LINK = (
    "Отлично! 🙌\n\n"
    "Напиши мне какое время тебе удобно (день + час, часовой пояс).\n"
    "Я работаю Пн–Пт, 10:00–18:00 CET."
)

MSG_MORE_INFO = (
    "Конечно 🙂\n\n"
    "*Что происходит на сессии:*\n"
    "• Ты рассказываешь о своём проекте и целях\n"
    "• Я нахожу 1-2 конкретные точки где AI даёт деньги/скорость\n"
    "• Ты уходишь с планом — не теорией, а следующим шагом\n\n"
    "*Для кого:*\n"
    "Предприниматели, фрилансеры, эксперты — "
    "те, кто работает самостоятельно и хочет AI-систему под свой бизнес.\n\n"
    "*Это бесплатно?*\n"
    "Да. Стратегическая сессия бесплатная.\n\n"
    "Готова записаться?"
)

MSG_DATE_RECEIVED = (
    "Записала! ✅\n\n"
    "Жди подтверждения в ближайшее время.\n"
    "До встречи! 🚀"
)

MSG_ADMIN_LEAD = (
    "🔔 *Новый лид в Liya AI Bot*\n\n"
    "👤 {name} (@{username})\n"
    "💬 О себе: {about}\n"
    "📅 Хочет записаться: {book_intent}\n"
    "🕐 {time}"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _user_label(user) -> str:
    name = (user.full_name or "").strip()
    username = f"@{user.username}" if user.username else "без username"
    return f"{name} ({username})"


async def _notify_admin(ctx, text: str) -> None:
    if not ADMIN_ID:
        return
    try:
        await ctx.bot.send_message(ADMIN_ID, text, parse_mode="Markdown")
    except Exception as exc:
        logger.warning("Admin notify failed: %s", exc)


# ---------------------------------------------------------------------------
# /start  +  code word trigger
# ---------------------------------------------------------------------------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✦ *Liya AI Bot*\n\n"
        f"Напиши кодовое слово *{CODE_WORD}* — и я расскажу как это работает.\n\n"
        "Или сразу: /help",
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✦ *Liya AI Bot*\n\n"
        f"Напиши *{CODE_WORD}* чтобы войти в воронку.\n\n"
        "Это бот Liya — системы *идея → деньги* через AI.",
        parse_mode="Markdown",
    )


async def code_word_trigger(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").upper()
    if CODE_WORD not in text:
        return ConversationHandler.END

    user = update.effective_user
    ctx.user_data["user_label"] = _user_label(user)
    ctx.user_data.clear()
    ctx.user_data["user_label"] = _user_label(user)

    await update.message.reply_text(MSG_WELCOME, parse_mode="Markdown")

    if VIDEO_URL:
        msg = MSG_VIDEO.format(video_url=VIDEO_URL)
    else:
        msg = MSG_VIDEO_NO_URL

    await update.message.reply_text(msg, parse_mode="Markdown")
    return F_QUALIFY


async def f_qualify(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["about"] = update.message.text

    buttons = [
        [InlineKeyboardButton("✅ Да, хочу на сессию", callback_data="book_yes")],
        [InlineKeyboardButton("📖 Расскажи подробнее",  callback_data="more_info")],
    ]
    await update.message.reply_text(MSG_EMPATHY, parse_mode="Markdown")
    await update.message.reply_text(
        MSG_OFFER,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

    await _notify_admin(
        ctx,
        MSG_ADMIN_LEAD.format(
            name=ctx.user_data.get("user_label", "—"),
            username=update.effective_user.username or "—",
            about=ctx.user_data.get("about", "—"),
            book_intent="в процессе",
            time="сейчас",
        ),
    )
    return F_BOOK


async def f_book_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if CALENDAR_LINK:
        msg = MSG_BOOK_YES.format(calendar_link=CALENDAR_LINK)
    else:
        msg = MSG_BOOK_YES_NO_LINK

    await query.edit_message_text(msg, parse_mode="Markdown")

    await _notify_admin(
        ctx,
        f"✅ *{ctx.user_data.get('user_label', '—')} нажал(а) «Хочу на сессию»*\n"
        f"О себе: {ctx.user_data.get('about', '—')}",
    )
    return F_DATE


async def f_more_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    buttons = [
        [InlineKeyboardButton("✅ Да, записаться!", callback_data="book_yes")],
        [InlineKeyboardButton("❌ Пока нет",        callback_data="book_no")],
    ]
    await query.edit_message_text(
        MSG_MORE_INFO,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return F_BOOK


async def f_book_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Хорошо! Буду здесь когда будешь готова.\n\n"
        f"Напиши *{CODE_WORD}* чтобы вернуться к этому разговору. 🙂",
        parse_mode="Markdown",
    )
    ctx.user_data.clear()
    return ConversationHandler.END


async def f_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["date_text"] = update.message.text
    await update.message.reply_text(MSG_DATE_RECEIVED, parse_mode="Markdown")

    await _notify_admin(
        ctx,
        f"📅 *{ctx.user_data.get('user_label', '—')} указал(а) время:*\n"
        f"{update.message.text}",
    )
    ctx.user_data.clear()
    return ConversationHandler.END


async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text(
        f"Окей, закрыла. Напиши *{CODE_WORD}* когда будешь готова.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# aiohttp health check
# ---------------------------------------------------------------------------
async def api_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "liya-ai-bot"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    tg_app = Application.builder().token(TG_TOKEN).build()

    funnel = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                code_word_trigger,
            )
        ],
        states={
            F_QUALIFY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, f_qualify)
            ],
            F_BOOK: [
                CallbackQueryHandler(f_book_yes,   pattern=r"^book_yes$"),
                CallbackQueryHandler(f_more_info,  pattern=r"^more_info$"),
                CallbackQueryHandler(f_book_no,    pattern=r"^book_no$"),
            ],
            F_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, f_date),
                CallbackQueryHandler(f_book_yes, pattern=r"^book_yes$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
        allow_reentry=True,
    )

    tg_app.add_handler(funnel)
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CommandHandler("help",  help_cmd))

    web_app = web.Application()
    web_app.router.add_get("/",       api_health)
    web_app.router.add_get("/health", api_health)

    async with tg_app:
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        logger.info("✦ Liya AI Bot запущен! Кодовое слово: %s", CODE_WORD)

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
