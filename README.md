# WA_bot — бот WhatsApp Web

Бот на Playwright (Firefox), который заходит по invite-ссылкам в группы WhatsApp
и отправляет в них сообщение. Ссылки лежат в `urls_list.py`.

## Требования

- Python 3.10+ (проверено на 3.14)
- Playwright 1.63 и скачанный браузер Firefox
- Linux / macOS / Windows

## Быстрый старт

Linux / macOS:

```bash
./setup.sh
.venv/bin/python main.py
```

Windows:

```bat
setup.bat
.venv\Scripts\python.exe main.py
```

Скрипт установки создаёт `.venv`, ставит зависимости, скачивает Firefox для
Playwright и создаёт пустой каталог `profiles/`.

## Установка вручную

```bash
python -m venv .venv                  # 1. окружение
source .venv/bin/activate             #    Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt   # 2. зависимости
python -m playwright install firefox  # 3. браузер (без него бот не запустится)
# Linux, если не хватает системных библиотек:
# python -m playwright install-deps firefox
mkdir -p profiles                     # 4. каталог профилей (создаётся и самим ботом)
python main.py                        # 5. запуск
```

В PyCharm: выберите интерпретатор `.venv` — зависимости из `requirements.txt`
подтянутся при синхронизации окружения, шаг с `playwright install firefox`
нужно выполнить из терминала один раз.

## Каталог `profiles/`

Внутри `profiles/` лежит по одному каталогу Firefox-профиля на каждый аккаунт
WhatsApp (например `profiles/whatsapp_profile1`). В профиле хранится сессия
WhatsApp Web (`cookies.sqlite`, `key4.db`, `storage`), то есть фактический доступ
к аккаунту, поэтому:

- содержимое `profiles/` **никогда не коммитится** (см. `.gitignore`);
- в git попадает только заглушка `profiles/.gitkeep`, чтобы папка появлялась
  сразу после клонирования проекта;
- каталог создаётся автоматически (`login_check.ensure_profiles_dir()`) при
  первом запуске — даже если `profiles/` отсутствует или пуст.

Чтобы добавить аккаунт, положите в `profiles/` каталог с уже выполненным входом
в WhatsApp Web (Firefox). Автоматической регистрации новых номеров пока нет —
она появится, когда бот подключат к базам.

## Статистика профиля: `bot_stats.json`

В каждом каталоге профиля бот ведёт файл `bot_stats.json` (например
`profiles/whatsapp_profile1/bot_stats.json`). Он создаётся автоматически
перед обходом ссылок и содержит:

- `kicked_count` — сколько групп, из которых аккаунт был выгнан, встретилось
  за текущий прогон (в начале работы с профилем счётчик обнуляется);
- `last_date_change` — время последней записи в файл (ISO-формат,
  например `2026-09-25T16:12:33`).

Файл лежит внутри `profiles/`, поэтому в git он не попадает.

## Как это работает

1. `main.py` — точка входа: настраивает логи, создаёт `profiles/`, запускает Playwright.
2. `login_check.py` — проверяет каждый профиль из `profiles/`
   (`check_profile()` открывает web.whatsapp.com) и делит их на `logged` /
   `unlogged`; если страница не открылась (сеть, DNS, прокси), профиль
   пропускается с ошибкой в логе, а остальные проверяются дальше.
3. `script.py` — для каждого залогиненного профиля создаёт/обнуляет
   `bot_stats.json`, проходит по ссылкам из `urls_list.py`, вступает в группу
   и отправляет сообщение; если аккаунт из группы выгнали, увеличивает
   `kicked_count`.
4. `logger.py` — логи в консоль и в `logs/wa_bot.log` с ротацией.

## Настройка

- Ссылки-приглашения: `urls_list.py` → `INVITE_URLS`.
- Уровень и файл логов: `logger.setup_logging(level=..., log_file=...)`.
- Каталог профилей: константа `PROFILES_DIR` в `login_check.py`
  (по умолчанию `<каталог проекта>/profiles`).

## Частые проблемы

- `Executable doesn't exist at .../firefox-...` — не выполнен
  `python -m playwright install firefox`.
- `Firefox не запускается, не хватает библиотек` (Linux) —
  `python -m playwright install-deps firefox` (нужен sudo).
- В логе `В .../profiles нет ни одного авторизованного профиля` — в `profiles/`
  ещё нет ни одного каталога с выполненным входом в WhatsApp Web.
- В логе `Не удалось открыть WhatsApp Web для профиля ...` и
  `Профиль ... пропущен: не удалось проверить` — страница не загрузилась
  (нет сети, блокировка, прокси). Такой профиль не попадает ни в `logged`,
  ни в `unlogged`, остальные проверяются дальше.

## Безопасность

Каталог `profiles/` (а также `logs/`) исключён из git. Не публикуйте содержимое
профилей: оно даёт полный доступ к аккаунту WhatsApp.
