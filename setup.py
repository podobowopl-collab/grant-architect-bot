"""
setup.py — автоматическая настройка всех ботов из .env

Что делает:
1. Загружает токены из .env
2. Проверяет подключение каждого бота
3. Устанавливает команды и описание через Telegram API
4. Проверяет доступ к GitHub

Запуск:
  python setup.py
"""
import os
import sys
import requests

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------
def load_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        print(f"❌  Файл {path} не найден.")
        print(f"    Скопируй шаблон: cp .env.example .env")
        print(f"    Заполни токены в .env и запусти снова.")
        sys.exit(1)
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if value and not os.environ.get(key.strip()):
                os.environ[key.strip()] = value.strip()


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
def tg_get(token: str, method: str, **params) -> dict:
    r = requests.get(
        f"https://api.telegram.org/bot{token}/{method}",
        params=params,
        timeout=10,
    )
    return r.json()


def tg_post(token: str, method: str, json: dict) -> dict:
    r = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=json,
        timeout=10,
    )
    return r.json()


def check_bot(token: str, label: str) -> bool:
    if not token:
        print(f"  ⚠  {label}: токен не задан, пропускаю")
        return False
    resp = tg_get(token, "getMe")
    if resp.get("ok"):
        bot = resp["result"]
        print(f"  ✅  {label}: @{bot['username']} ({bot['first_name']})")
        return True
    else:
        print(f"  ❌  {label}: ошибка — {resp.get('description', 'неизвестно')}")
        return False


def set_grant_commands(token: str) -> None:
    commands = [
        {"command": "start",     "description": "Начало работы"},
        {"command": "apply",     "description": "Wizard заявки на грант (6 шагов)"},
        {"command": "grant",     "description": "Загрузить файл в /grants"},
        {"command": "project",   "description": "Загрузить файл в /projects"},
        {"command": "knowledge", "description": "Загрузить файл в /knowledge"},
        {"command": "upload",    "description": "Загрузить файл в /uploads"},
        {"command": "search",    "description": "Поиск файлов по имени"},
        {"command": "help",      "description": "Помощь"},
        {"command": "cancel",    "description": "Отменить текущий wizard"},
    ]
    resp = tg_post(token, "setMyCommands", {"commands": commands})
    if resp.get("ok"):
        print("     → Команды установлены")
    else:
        print(f"     → Ошибка установки команд: {resp.get('description')}")

    desc = (
        "Grant Architect Bot — помогаю собрать заявку на грант.\n"
        "Wizard /apply: 6 вопросов → черновик заявки + сохранение."
    )
    tg_post(token, "setMyDescription", {"description": desc})
    tg_post(token, "setMyShortDescription", {"short_description": "Wizard заявки на грант"})
    print("     → Описание бота установлено")


def set_igi_commands(token: str) -> None:
    commands = [
        {"command": "start",  "description": "Начало работы"},
        {"command": "tunnel", "description": "Wizard смыслового туннеля (5 шагов)"},
        {"command": "help",   "description": "Помощь"},
        {"command": "cancel", "description": "Отменить текущий wizard"},
    ]
    resp = tg_post(token, "setMyCommands", {"commands": commands})
    if resp.get("ok"):
        print("     → Команды установлены")
    else:
        print(f"     → Ошибка установки команд: {resp.get('description')}")

    desc = (
        "IGI Bot — Idea → Get Income.\n"
        "5 вопросов → смысловой туннель: hook, CTA, DM-скрипт, воронка."
    )
    tg_post(token, "setMyDescription", {"description": desc})
    tg_post(token, "setMyShortDescription", {"short_description": "Idea → Get Income"})
    print("     → Описание бота установлено")


# ---------------------------------------------------------------------------
# GitHub check
# ---------------------------------------------------------------------------
def check_github(token: str, owner: str, repo: str) -> None:
    if not token:
        print("  ⚠  GitHub: токен не задан, пропускаю")
        return
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    r = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}",
        headers=headers,
        timeout=10,
    )
    if r.status_code == 200:
        data = r.json()
        print(f"  ✅  GitHub: {owner}/{repo} — доступ есть")
        print(f"     → Репозиторий: {data.get('full_name')}")
    elif r.status_code == 404:
        print(f"  ⚠  GitHub: репозиторий {owner}/{repo} не найден")
        print(f"     → Создай его на github.com и запусти снова")
    else:
        print(f"  ❌  GitHub: ошибка {r.status_code} — {r.json().get('message')}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print("\n✦ Настройка ботов — Lia Strategy and AI Automation\n")

    load_env()

    grant_token = os.environ.get("TG_BOT_TOKEN", "")
    igi_token   = os.environ.get("IGI_BOT_TOKEN", "")
    gh_token    = os.environ.get("GITHUB_TOKEN", "")
    gh_owner    = os.environ.get("GITHUB_OWNER", "podobowopl-collab")
    gh_repo     = os.environ.get("GITHUB_REPO", "GRANT-AGENT-COURSE")

    print("── Telegram боты ──────────────────────────────────────")

    if check_bot(grant_token, "Grant Architect Bot"):
        set_grant_commands(grant_token)

    if check_bot(igi_token, "IGI Bot"):
        set_igi_commands(igi_token)

    print("\n── GitHub ─────────────────────────────────────────────")
    check_github(gh_token, gh_owner, gh_repo)

    print("\n── Итог ───────────────────────────────────────────────")
    print("Что делать дальше:")
    print("  1. Деплой Grant Architect Bot → Render (TG_BOT_TOKEN + GITHUB_TOKEN)")
    print("  2. Деплой IGI Bot → отдельный Render сервис (IGI_BOT_TOKEN)")
    print("  3. Добавить ботов в нужные Telegram-группы вручную")
    print("     (это должен сделать человек — боты не могут войти в группу сами)")
    print()


if __name__ == "__main__":
    main()
