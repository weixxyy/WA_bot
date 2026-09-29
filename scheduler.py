"""Планировщик запусков бота по времени суток.

Бот работает не постоянно, а «просыпается» в заданные моменты (по умолчанию —
:data:`DEFAULT_RUN_AT`). Ближайшее время считается заново от текущего момента
после каждого прогона, поэтому долгий прогон (Playwright обходит все группы)
не накладывается сам на себя: слоты, которые прогон «пересидел», просто
пропускаются и не догоняются.

Пример использования::

    from scheduler import run_forever

    run_forever(run_once)                      # 09:00 и 18:00
    run_forever(run_once, ["10:30", "22:15"])  # свои времена

Запуск по расписанию — ``python main.py``, одиночный прогон — ``python main.py
--once`` (см. ``main.py``).
"""

import time
from datetime import datetime, time as dt_time, timedelta

from logger import get_logger

log = get_logger(__name__)

# Времена запусков в местном времени машины, формат "ЧЧ:ММ".
DEFAULT_RUN_AT = ("09:00", "18:00")

# Формат одного слота в DEFAULT_RUN_AT и --at.
TIME_FORMAT = "%H:%M"


def parse_run_at(values) -> list[dt_time]:
    """Превращает времена запусков в отсортированный список без дублей.

    Принимает строки ``"ЧЧ:ММ"`` и уже разобранные ``datetime.time`` (повторный
    разбор безопасен), поэтому одну и ту же функцию используют ``--at`` и
    :func:`run_forever`.

    :raises ValueError: если строка не похожа на время в формате "ЧЧ:ММ".
    """
    parsed = []
    for value in values:
        if isinstance(value, dt_time):
            moment = value
        else:
            try:
                moment = datetime.strptime(value, TIME_FORMAT).time()
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"Некорректное время запуска {value!r}: ожидается формат "
                    f"ЧЧ:ММ, например 09:00"
                ) from error
        if moment not in parsed:
            parsed.append(moment)
    return sorted(parsed)


def next_run_at(times, now=None) -> datetime:
    """Ближайший момент из ``times``, который ещё не наступил.

    Если все слоты на сегодня уже прошли, возвращается первый слот на завтра.

    :param times: список ``datetime.time`` (порядок не важен).
    :param now: текущий момент; параметр нужен для тестов и по умолчанию
        берётся ``datetime.now()``.
    """
    now = now or datetime.now()
    for moment in sorted(times):
        candidate = now.replace(
            hour=moment.hour,
            minute=moment.minute,
            second=0,
            microsecond=0,
        )
        if candidate > now:
            return candidate

    # Все слоты на сегодня уже прошли — ждём первый слот завтра.
    first = min(times)
    tomorrow = now + timedelta(days=1)
    return tomorrow.replace(
        hour=first.hour,
        minute=first.minute,
        second=0,
        microsecond=0,
    )


def run_forever(job, times=DEFAULT_RUN_AT) -> None:
    """Бесконечно вызывает ``job()`` в заданные времена суток.

    Исключение внутри ``job`` не прерывает планировщик: оно попадает в лог,
    а следующий запуск всё равно планируется. Останавливается по Ctrl+C —
    ``KeyboardInterrupt`` уходит наружу, в точку входа.

    :param job: вызываемый объект без аргументов — один прогон бота.
    :param times: времена запусков: строки "ЧЧ:ММ" или ``datetime.time``.
    :raises ValueError: если список времён пуст.
    """
    times = parse_run_at(times)
    if not times:
        raise ValueError("Список времён запуска пуст: планировать нечего")

    log.info(
        "Планировщик запущен. Времена запусков: %s",
        ", ".join(moment.strftime(TIME_FORMAT) for moment in times),
    )

    while True:
        moment = next_run_at(times)
        delay = (moment - datetime.now()).total_seconds()
        log.info(
            "Следующий запуск: %s (через %.1f мин)",
            moment.strftime("%Y-%m-%d %H:%M"),
            delay / 60,
        )

        time.sleep(max(delay, 0.0))

        log.info(
            "Запуск по расписанию: %s",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        try:
            job()
        except Exception:
            # Упавший прогон не должен убивать планировщик: ждём следующий слот.
            log.exception("Прогон завершился ошибкой, планировщик продолжает работу")
        else:
            log.info("Прогон завершён")
