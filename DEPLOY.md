# Деплой max_support_bot

## Хостинг Джино (Jino.ru)

Официальная подготовка: услуги «Поддержка веб-приложений» и SSH, выбор интерпретатора Python для домена, при необходимости виртуальное окружение ([общая статья](https://jino.ru/spravka/hosting/articles/deploy.html), [деплой через Git](https://jino.ru/spravka/hosting/articles/deploy_git.html)).

После `git pull` в каталоге проекта на сервере:

```bash
chmod +x deploy/jino_deploy.sh
./deploy/jino_deploy.sh
```

Скрипт создаёт/обновляет `.venv`, ставит зависимости из `requirements.txt`, создаёт каталоги `data/*`. Файл `.env` нужно выложить на сервер отдельно (секреты не в git).

Для долгоживущего процесса бота на своём сервере можно взять за основу `deploy/systemd/max-support-bot.service.example` (на shared-хостинге чаще используют запуск из панели или `screen`/`tmux` по инструкции провайдера).

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
- каталоги `app/`, `models/`, `services/`, `utils/`, `keyboards/`, `data/`, `web_admin/`
- при необходимости `.env`

## После обновления

1. Установить зависимости: `pip install -r requirements.txt`
2. Перезапустить процесс бота (systemd / панель хостинга).
3. Удалить устаревший кэш при подозрении на старый код:  
   `find . -type d -name __pycache__ -exec rm -rf {} +` (осторожно, в каталоге проекта).

При старте в логах должна быть строка вида:  
`Сборка: max_support_bot admin-modules (app/admin_panel + documents + system)`.

## База данных

При первом запуске после обновления `init_db()` создаст таблицы `instruction_documents` и `system_settings`. Старый файл `*.db` совместим: добавятся только новые таблицы.

## Веб-админка Flask (браузер)

Отдельный процесс, **та же БД** и **тот же пароль**, что для `/admin` в боте:

```bash
cd /path/to/max_support_bot
.venv/bin/python -m web_admin
```

По умолчанию: `http://127.0.0.1:5000`. В `.env` задайте длинный `WEB_ADMIN_SECRET_KEY`. Для продакшена — только за **HTTPS** (nginx/Caddy reverse proxy).

Пример nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

## Веб-админка Flask (браузер)

Запуск отдельно от бота, **та же БД** и **тот же пароль**, что для `/admin` в MAX:

```bash
cd /path/to/max_support_bot
.venv/bin/python -m web_admin
```

По умолчанию слушает `127.0.0.1:5000`. Задайте в `.env` длинный `WEB_ADMIN_SECRET_KEY`. Для продакшена — **HTTPS** (nginx/Caddy как reverse proxy), не публикуйте порт без TLS.

Пример фрагмента nginx (замените домен и путь к сокету/порту):

```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```
