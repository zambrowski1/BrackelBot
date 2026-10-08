# SPDX-License-Identifier: MIT
from difflib import unified_diff


def generate_diff(before: str, after: str, title: str) -> str:
    return "".join(unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                fromfile=f"{title} (исходный)", tofile=f"{title} (предпросмотр)", n=3))
