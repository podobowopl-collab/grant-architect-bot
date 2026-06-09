"""
IGI Bot — Idea → Get Income
Собирает смысловой туннель (Meaning Tunnel) под любой бизнес.
5 вопросов → структура: hook, тезис, CTA, DM-скрипт, воронка 3 шага.

Env vars:
  IGI_BOT_TOKEN  — Telegram bot token
  PORT           — HTTP port (default 8080)
"""
import asyncio
import logging
import os
import signal

from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
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

TG_TOKEN = os.environ.get("IGI_BOT_TOKEN")
PORT     = int(os.environ.get("PORT", 8080))

if not TG_TOKEN:
    raise ValueError("IGI_BOT_TOKEN is not set")

# ---------------------------------------------------------------------------
# Wizard states
# ---------------------------------------------------------------------------
I_PRODUCT, I_CLIENT, I_PAIN, I_RESULT, I_NEXT_STEP = range(5)


# ---------------------------------------------------------------------------
# Tunnel generator
# ---------------------------------------------------------------------------
def build_tunnel(d: dict) -> str:
    product   = d.get("product", "")
    client    = d.get("client", "")
    pain      = d.get("pain", "")
    result    = d.get("result", "")
    next_step = d.get("next_step", "")

    return (
        f"# Смысловой туннель — {product}\n\n"
        f"---\n\n"
        f"## Аудитория\n\n"
        f"**Кто:** {client}\n"
        f"**Боль:** {pain}\n"
        f"**Хочет получить:** {result}\n\n"
        f"---\n\n"
        f"## Hook (первая фраза рилса / поста)\n\n"
        f"> Ты [{pain.split('.')[0].lower() if pain else 'не знаешь с чего начать'}] — "
        f"и именно поэтому у тебя пока нет {result.split('.')[0].lower() if result else 'результата'}.\n\n"
        f"---\n\n"
        f"## Тезис (2–3 строки)\n\n"
        f"Большинство людей в твоей ситуации думают, что проблема в [{pain}]. "
        f"На самом деле проблема в том, что нет одного правильного шага. "
        f"Я покажу тебе этот шаг.\n\n"
        f"---\n\n"
        f"## CTA (призыв к действию)\n\n"
        f"Напиши мне [{next_step}] — и я покажу, с чего начать именно тебе.\n\n"
        f"---\n\n"
        f"## DM-скрипт (шаблон ответа в директ)\n\n"
        f"Когда человек пишет [{next_step}]:\n\n"
        f"1. «Привет! Расскажи коротко о своём проекте — что делаешь, где сейчас?\"\n"
        f"2. «Понял. Главный вопрос: что сейчас мешает получить {result}?\"\n"
        f"3. «Отлично. Вот что я предлагаю: [{product}]. "
        f"Это даёт {result}. Хочешь обсудим детали?\"\n\n"
        f"---\n\n"
        f"## Воронка: 3 шага от незнакомца до продажи\n\n"
        f"```\n"
        f"Шаг 1 — ОСОЗНАНИЕ\n"
        f"  Человек видит пост/рилс с hook про [{pain}]\n"
        f"  Думает: «Это про меня»\n\n"
        f"Шаг 2 — ДОВЕРИЕ\n"
        f"  Пишет кодовое слово [{next_step}]\n"
        f"  Получает персональный ответ + первую пользу\n\n"
        f"Шаг 3 — ПРОДАЖА\n"
        f"  Понимает, что {product} даёт ему {result}\n"
        f"  Делает следующий шаг: созвон / оплата\n"
        f"```\n\n"
        f"---\n"
        f"*Сгенерировано IGI Bot — Idea → Get Income*\n"
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✦ *IGI Bot — Idea → Get Income*\n\n"
        "Я помогаю собрать смысловой туннель под любой бизнес:\n"
        "от идеи до продажи через 5 вопросов.\n\n"
        "Напиши /tunnel чтобы начать.\n"
        "Напиши /help чтобы узнать больше.",
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✦ *Помощь — IGI Bot*\n\n"
        "/tunnel — запустить wizard смыслового туннеля\n\n"
        "*Что ты получишь на выходе:*\n"
        "• Hook для рилса или поста\n"
        "• Тезис (2–3 строки)\n"
        "• CTA — кодовое слово / призыв\n"
        "• DM-скрипт — как отвечать в директ\n"
        "• Воронка в 3 шага: осознание → доверие → продажа\n\n"
        "Работает для любой ниши: курсы, услуги, продукты, консультации.",
        parse_mode="Markdown",
    )


async def tunnel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text(
        "🌀 *Wizard смыслового туннеля*\n\n"
        "5 вопросов → готовая структура воронки.\n"
        "Напиши /cancel чтобы выйти.\n\n"
        "*Вопрос 1 из 5*\n"
        "Что ты продаёшь?\n"
        "_(курс, услуга, консультация, продукт — опиши одной фразой)_",
        parse_mode="Markdown",
    )
    return I_PRODUCT


async def i_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["product"] = update.message.text
    await update.message.reply_text(
        "*Вопрос 2 из 5*\n"
        "Кто твой клиент?\n"
        "Опиши одного конкретного человека — кто он, чем занимается?",
        parse_mode="Markdown",
    )
    return I_CLIENT


async def i_client(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["client"] = update.message.text
    await update.message.reply_text(
        "*Вопрос 3 из 5*\n"
        "Какая его главная боль?\n"
        "Что его мучает каждый день — что не получается, что раздражает?",
        parse_mode="Markdown",
    )
    return I_PAIN


async def i_pain(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["pain"] = update.message.text
    await update.message.reply_text(
        "*Вопрос 4 из 5*\n"
        "Что получает клиент после работы с тобой?\n"
        "Конкретный результат — что изменится в его жизни/бизнесе?",
        parse_mode="Markdown",
    )
    return I_RESULT


async def i_result(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["result"] = update.message.text
    await update.message.reply_text(
        "*Вопрос 5 из 5*\n"
        "Какой следующий шаг ты хочешь от человека после поста/рилса?\n"
        "_(кодовое слово в директ / написать «хочу» / записаться на созвон)_",
        parse_mode="Markdown",
    )
    return I_NEXT_STEP


async def i_next_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["next_step"] = update.message.text

    await update.message.reply_text(
        "⏳ Собираю твой смысловой туннель...", parse_mode="Markdown"
    )

    tunnel = build_tunnel(ctx.user_data)

    header = "✅ *Смысловой туннель готов!*\n\n"
    full_text = header + tunnel

    if len(full_text) <= 4096:
        await update.message.reply_text(full_text, parse_mode="Markdown")
    else:
        await update.message.reply_text(header, parse_mode="Markdown")
        for i in range(0, len(tunnel), 4000):
            await update.message.reply_text(
                f"```\n{tunnel[i:i+4000]}\n```", parse_mode="Markdown"
            )

    await update.message.reply_text(
        "💡 *Следующие шаги:*\n\n"
        "1. Возьми hook и напиши первый пост/рилс\n"
        "2. Настрой ответ на кодовое слово в боте\n"
        "3. Используй DM-скрипт для разговора\n\n"
        "Напиши /tunnel чтобы собрать туннель для другого проекта.",
        parse_mode="Markdown",
    )

    ctx.user_data.clear()
    return ConversationHandler.END


async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text(
        "❌ Wizard отменён. Напиши /tunnel чтобы начать заново."
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# aiohttp web server
# ---------------------------------------------------------------------------
async def api_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "igi-bot"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    tg_app = Application.builder().token(TG_TOKEN).build()

    tunnel_wizard = ConversationHandler(
        entry_points=[CommandHandler("tunnel", tunnel_cmd)],
        states={
            I_PRODUCT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, i_product)],
            I_CLIENT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, i_client)],
            I_PAIN:      [MessageHandler(filters.TEXT & ~filters.COMMAND, i_pain)],
            I_RESULT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, i_result)],
            I_NEXT_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, i_next_step)],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    tg_app.add_handler(tunnel_wizard)
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CommandHandler("help",  help_cmd))

    web_app = web.Application()
    web_app.router.add_get("/",       api_health)
    web_app.router.add_get("/health", api_health)

    async with tg_app:
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        logger.info("✦ IGI Bot запущен!")

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
