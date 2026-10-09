# SPDX-License-Identifier: MIT
import json
from pathlib import Path
from .errors import UpdateError
from .schema_validator import validate_package


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError("invalid_input", f"Повторный ключ JSON: {key}")
        result[key] = value
    return result


def load_json(path: str | Path) -> dict:
    try:
        if Path(path).stat().st_size > 10_000_000:
            raise UpdateError("invalid_input", "JSON превышает 10 МБ")
        package = json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=_object,
                             parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise UpdateError("invalid_input", f"Не удалось загрузить JSON: {exc}") from exc
    return package


def load_package(path: str | Path) -> dict:
    package=load_json(path)
    validate_package(package)
    return package
