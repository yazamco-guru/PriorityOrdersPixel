"""בחירת מפענח להזמנה.

תבניות לפי מקור נבדקות לפי הסדר; אם אף אחת לא מתאימה – פענוח כללי.
תבנית = (שם, detect(doc, mail) -> bool, parse(doc, mail) -> dict באותו מבנה של generic.parse).
"""
from . import generic

TEMPLATES = []


def parse(doc, mail):
    for name, detect, fn in TEMPLATES:
        if detect(doc, mail):
            return {**fn(doc, mail), 'parser': name}
    return {**generic.parse(doc, mail), 'parser': 'כללי'}
