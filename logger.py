"""Настройка логирования для всего проекта.

Логи пишутся одновременно в консоль (stdout) и в файл logs/wa_bot.log
с ротацией. Один раз вызываем :func:`setup_logging` в точке входа (main.py),
а в остальных модулях берём логгер через :func:`get_logger`.

Пример использования::

    from logger import get_logger, setup_logging

    log = get_logger(__name__)

    def main():
        setup_logging()
        log.info("Поехали")
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from paths import LOG_FILE

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Имя корневого логгера проекта: модули получают дочерние wa_bot.<module>.
LOGGER_NAME = "wa_bot"

MAX_BYTES = 1024 * 1024
BACKUP_COUNT = 3


def setup_logging(level=logging.INFO, log_file=LOG_FILE):
    """Настраивает корневой логгер проекта и возвращает его.

    Повторный вызов безопасен: старые обработчики удаляются, поэтому дубли
    записей не появляются.
    """
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    # Свои обработчики есть, наверх (в root) сообщения не отдаём,
    # иначе можно получить дубли, если root настроят отдельно.
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


def get_logger(name=None):
    """Возвращает дочерний логгер проекта: ``wa_bot.<name>``.

    :param name: обычно ``__name__`` модуля; ``None`` — корневой логгер.
    """
    logger = logging.getLogger(LOGGER_NAME)
    if name is None:
        return logger
    return logger.getChild(name)
