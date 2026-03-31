#!/usr/bin/env bash
# Авторазвёртывание max_support_bot на хостинге Джино после git pull.
# См. официальные инструкции:
#   https://jino.ru/spravka/hosting/articles/deploy.html
#   https://jino.ru/spravka/hosting/articles/deploy_git.html
#
# На сервере Джино: включите «Поддержку веб-приложений», SSH, выберите Python
# в настройках домена, создайте venv по их гайду (python.html#script-pyth-mod).
#
# Использование (в каталоге клона репозитория на хостинге):
#   chmod +x deploy/jino_deploy.sh
#   ./deploy/jino_deploy.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-$ROOT/.venv}"

if [[ ! -d "$VENV" ]]; then
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck source=/dev/null
source "$VENV/bin/activate"
pip install --upgrade pip
pip install -r requirements.txt

mkdir -p data/instructions data/temp data/ticket_photos
if [[ ! -f .env ]]; then
  echo "Внимание: создайте .env (скопируйте из .env.example и задайте BOT_TOKEN и др.)."
fi

echo "Готово. Перезапустите процесс бота (systemd/supervisor/панель хостинга)."
echo "Пример unit-файла: deploy/systemd/max-support-bot.service.example"
