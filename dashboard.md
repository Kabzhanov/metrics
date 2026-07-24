# AI Models Metrics Dashboard (задача #1163)

Публичный дашборд: **https://bizdnai.com/metrics/** — визуализация метрик AI-моделей из таблицы `metrics_tasks` + view `metrics_tasks_v` (БД `bizdnai:5434`).

## Что показывает

- 4 KPI вверху: Tasks / Success Rate / Avg Duration / Total Cost за выбранный период
- 3 Line-графика (Chart.js): Success Rate, Avg Duration (sec), Avg Cost (USD) по дням, отдельная линия на каждую модель
- 1 Bar-график: общее число задач по моделям за период
- Таблица: последние 20 задач (task_id / model / status / duration / cost / started)
- Фильтры: период (7d / 30d / 90d) и модель (dropdown)

Авто-обновление каждые 5 минут (клиентский setInterval).

## Компоненты

- `/var/www/bizdnai.com/metrics/index.html` — разметка
- `/var/www/bizdnai.com/metrics/style.css` — тёмная тема
- `/var/www/bizdnai.com/metrics/app.js` — клиент: fetch + Chart.js
- `/var/www/bizdnai.com/metrics/api.php` — JSON endpoint, параметры `?period=7|30|90&model=<name>`

## Nginx

В `/etc/nginx/sites-enabled/bizdnai` (перед `location / {`):
- `location /metrics/` — alias, статика
- `location = /metrics/api.php` — fastcgi → `/run/php/php-fpm.sock`

## Зависимости

- PHP-FPM 8.3 + расширение `pdo_pgsql` (установлено `apt install php8.3-pgsql`)
- Chart.js 4.4.1 через CDN
- Подключение к БД: параметры `METRICS_DB_HOST`, `METRICS_DB_PORT`, `METRICS_DB_NAME`, `METRICS_DB_USER` и `METRICS_DB_PASSWORD` передаются через секреты окружения; значения не хранятся в репозитории.

## Smoke-тесты

- `curl -sI https://bizdnai.com/metrics/` → 200, HTML
- `https://bizdnai.com/metrics/api.php?period=30d` → 200, валидный JSON
- `nginx -t` → syntax ok, `nginx -s reload` без ошибок

## Примечание

Таблицы `metrics_tasks` и view `metrics_tasks_v` пока **пустые** (0 строк) — дашборд работает корректно (показывает «0», «Нет данных»). При появлении данных графики заполнятся автоматически.
