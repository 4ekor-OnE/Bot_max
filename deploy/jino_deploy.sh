#!/usr/bin/env bash
# Авторазвёртывание max_support_bot на хостинге Джино после git pull.
# Официальные инструкции:
#   https://jino.ru/spravka/hosting/articles/deploy.html
#   https://jino.ru/spravka/hosting/articles/deploy_git.html
# Создание venv:
#   https://jino.ru/spravka/hosting/python.html#script-pyth-mod
#
# На сервере Джино: включите «Поддержку веб-приложений», SSH, выберите интерпретатор
# Python в настройках домена и при необходимости создайте venv по ссылке выше.
#
# Запуск (из корня клона репозитория на хостинге):
#   chmod +x deploy/jino_deploy.sh
#   ./deploy/jino_deploy.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-$ROOT/.venv}"
PIP_INSTALL=("$VENV/bin/python" -m pip)

if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV"
fi

"${PIP_INSTALL[@]}" install --upgrade pip
"${PIP_INSTALL[@]}" install -r requirements.txt

mkdir -p data/instructions data/temp data/ticket_photos
if [[ ! -f .env ]]; then
  echo "Внимание: создайте файл .env (скопируйте из .env.example и задайте BOT_TOKEN или MAX_BOT_TOKEN и др.)." >&2
fi

echo "Готово. Перезапустите процесс бота (systemd, supervisor или панель хостинга)."
echo "Пример unit-файла: deploy/systemd/max-support-bot.service.example"
