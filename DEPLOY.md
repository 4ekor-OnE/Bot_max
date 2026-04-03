# Деплой max_support_bot

## Хостинг Джино (Jino.ru)

### Подготовка хостинга

В панели Джино подключите **«Поддержку веб-приложений»** и **SSH**; для домена выберите интерпретатор **Python**. Официальные материалы:

- [Пример деплоя Python-приложения](https://jino.ru/spravka/hosting/articles/deploy.html)
- [Деплой через Git](https://jino.ru/spravka/hosting/articles/deploy_git.html)
- [Создание виртуального окружения (venv)](https://jino.ru/spravka/hosting/python.html#script-pyth-mod)

### Скрипт `deploy/jino_deploy.sh`

Запускайте из **корня клона** репозитория (каталог, где лежат `bot.py` и `requirements.txt`), а не из `deploy/`:

```bash
chmod +x deploy/jino_deploy.sh
./deploy/jino_deploy.sh
```

Что делает скрипт:

- если нет исполняемого `.venv/bin/python`, создаёт окружение командой `"$PYTHON" -m venv "$VENV"` (по умолчанию `PYTHON=python3`, `VENV=$ROOT/.venv`);
- обновляет **pip** и ставит зависимости из `requirements.txt` через `"$VENV/bin/python" -m pip` (без `source activate`, удобнее в неинтерактивном SSH);
- создаёт каталоги `data/instructions`, `data/temp`, `data/ticket_photos`.

**`.env`** на сервер кладётся отдельно (секреты не в git): скопируйте `.env.example` в `.env` и задайте `BOT_TOKEN` или `MAX_BOT_TOKEN` и прочие переменные. Если `.env` нет, скрипт всё равно завершится успешно, но выведет предупреждение в stderr.

После выполнения скрипта **перезапустите процесс бота** способом, который допускает ваш тариф (панель, `screen`/`tmux`, свой процесс-менеджер). На **VPS** ориентир — **`deploy/systemd/max-support-bot.service.example`**.

**Первый деплой:** `git clone` → создать `.env` → `./deploy/jino_deploy.sh` → запуск `.venv/bin/python bot.py` (и при необходимости `.venv/bin/python -m web_admin`).

**Обновление:** `git pull` → `./deploy/jino_deploy.sh` → перезапуск бота и при необходимости веб-админки.

## Каталог `deploy/`

| Файл | Назначение |
|------|------------|
| `jino_deploy.sh` | Обновление venv и зависимостей, создание `data/*` после `git pull` на Джино |
| `systemd/max-support-bot.service.example` | Пример unit-файла для systemd на VPS; пути и `User=` задайте под свой сервер |

## Почему админка показывает «будет реализована на следующем этапе»

Так отвечает **старая** `bot.py`, где разделы админки были заглушками. В актуальной версии обработка идёт через:

- `app/admin_panel.py`
- `app/admin_documents_flow.py`
- `app/admin_system_flow.py`
- `app/admin_common.py`

Если на сервер залили только часть файлов или не перезапустили процесс, будет прежнее поведение или ошибки импорта.

## Что залить на сервер

Весь каталог проекта, включая:

- `bot.py`, `config.py`, `cleanup.py`, `requirements.txt`
- каталоги `app/`, `models/`, `services/`, `utils/`, `keyboards/`, `data/`, `web_admin/`, `deploy/`
- при необходимости `.env`

## После обновления

1. Зависимости: на Джино — **`./deploy/jino_deploy.sh`**; вручную — `pip install -r requirements.txt` в активированном venv или `.venv/bin/python -m pip install -r requirements.txt`.
2. Перезапустить процесс бота (systemd / панель хостинга / `screen` / `tmux`).
3. При подозрении на старый байткод удалить кэш в каталоге проекта:  
   `find . -type d -name __pycache__ -exec rm -rf {} +` (выполняйте осознанно, только внутри проекта).

При старте в логах должна быть строка вида:  
`Сборка: max_support_bot admin-modules (app/admin_panel + documents + system)`.

## База данных

При первом запуске после обновления `init_db()` создаст таблицы `instruction_documents` и `system_settings`. Старый файл `*.db` совместим: добавятся только новые таблицы.

## Веб-админка Flask (браузер)

Отдельный процесс, **та же БД** и **тот же пароль**, что для `/admin` в боте MAX:

```bash
cd /path/to/max_support_bot
.venv/bin/python -m web_admin
```

По умолчанию: `http://127.0.0.1:5000`. В `.env` задайте длинный `WEB_ADMIN_SECRET_KEY`. Для продакшена — только за **HTTPS** (nginx/Caddy reverse proxy); не публикуйте порт без TLS.

Пример фрагмента nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```
