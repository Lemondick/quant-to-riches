"""Exact serialization and bounded local ledger review projections."""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


def encoded(value):
    def default(item):
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, (Decimal, date, datetime)):
            return str(item)
        raise TypeError(type(item).__name__)

    return json.dumps(value, default=default, ensure_ascii=False, sort_keys=True)


def public(value):
    return json.loads(encoded(value))


def rows(connection, sql, parameters=()):
    cursor = connection.execute(sql, parameters)
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def audit(connection, category, identity, now, before, after):
    connection.execute(
        "INSERT INTO audit_log(entity_type,entity_id,changed_at,before_json,after_json) "
        "VALUES(?,?,?,?,?)",
        [category, str(identity), now, encoded(before), encoded(after)],
    )
