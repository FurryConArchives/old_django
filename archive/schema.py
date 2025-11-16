"""Lightweight schema fixes for databases that are not managed with migrations."""

import logging

from django.db import connection
from django.db.utils import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)


def ensure_appkey_scope_column():
    """Widen archive_appkey.scope to match the packed scopes+rate encoding.

    The column was originally VARCHAR(20) for legacy values ``convention`` / ``all``.
    Saving comma-separated scopes overflows that width under MySQL STRICT mode.
    Returns True if the column is wide enough afterwards.
    """
    from .models import AppKey

    if connection.vendor == 'sqlite':
        return True
    if connection.vendor != 'mysql':
        return False

    field = AppKey._meta.get_field('scope')
    table = AppKey._meta.db_table
    wanted = int(field.max_length or 255)

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT CHARACTER_MAXIMUM_LENGTH
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                  AND COLUMN_NAME = 'scope'
                """,
                [table],
            )
            row = cursor.fetchone()
            if not row or row[0] is None:
                return False
            current = int(row[0])
            if current >= wanted:
                return True
            quoted = connection.ops.quote_name(table)
            cursor.execute(
                f"ALTER TABLE {quoted} MODIFY COLUMN `scope` VARCHAR({wanted}) NOT NULL DEFAULT 'convention'"
            )
            return True
    except (OperationalError, ProgrammingError):
        return False
    except Exception:
        logger.warning('Could not widen %s.scope to VARCHAR(%s)', table, wanted, exc_info=True)
        return False
