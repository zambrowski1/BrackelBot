# SPDX-License-Identifier: MIT
import mwparserfromhell as mw


def category_label(value):
    code=mw.parse(value)
    for tag in reversed(code.filter_tags()):
        if str(tag.tag).casefold() in {'ref','br'}:code.remove(tag)
    for template in code.filter_templates():
        if str(template.name).strip().casefold()=='efn':code.remove(template)
    return code.strip_code().strip()
