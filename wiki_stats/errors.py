# SPDX-License-Identifier: MIT
class UpdateError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)
