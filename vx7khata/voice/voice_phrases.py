"""Turning amounts into spoken words.

"₹1,250.50" read aloud by a speech engine comes out as "one thousand two hundred fifty point five", which no
shopkeeper wants to hear. Every amount the assistant speaks goes through here first so it lands as
"एक हज़ार दो सौ पचास रुपये पचास पैसे" (or the English / Roman-Hindi equivalent).

Hindi 21-99 does not compose regularly - इक्कीस is not "एक बीस" - so the compound decades are listed in full
rather than generated.
"""
from __future__ import annotations

_ZERO = ("शून्य", "shoonya", "zero")
_UNITS = (
    ("एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ"),
    ("ek", "do", "teen", "chaar", "paanch", "chhah", "saat", "aath", "nau"),
    ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine"),
)
_TEENS = (
    ("दस", "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह", "उन्नीस"),
    ("das", "gyaarah", "baarah", "terah", "chaudah", "pandrah", "solah", "satrah", "athaarah", "unnees"),
    ("ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"),
)
_TENS = (
    ("बीस", "तीस", "चालीस", "पचास", "साठ", "सत्तर", "अस्सी", "नब्बे"),
    ("bees", "tees", "chaalees", "pachaas", "saath", "sattar", "assi", "nabbe"),
    ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"),
)
# 21-29, 31-39, ... 91-99 read as one word each; index is [decade - 2][unit - 1].
_COMPOUND = (
    (("इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस", "छब्बीस", "सत्ताईस", "अट्ठाईस", "उनतीस"),
     ("इकतीस", "बत्तीस", "तैंतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस", "उनतालीस"),
     ("इकतालीस", "बयालीस", "तैंतालीस", "चवालीस", "पैंतालीस", "छियालीस", "सैंतालीस", "अड़तालीस", "उनचास"),
     ("इक्यावन", "बावन", "तिरेपन", "चवन", "पेंचवन", "छप्पन", "सत्तावन", "अट्ठावन", "उनसठ"),
     ("इकसठ", "बासठ", "तिरेसठ", "चूसठ", "पैंसठ", "छसठ", "सैंसठ", "अड़सठ", "उनहत्तर"),
     ("इकहत्तर", "बहत्तर", "तिहत्तर", "चिहत्तर", "पचहत्तर", "छिहत्तर", "सतहत्तर", "अठहत्तर", "उन्यासी"),
     ("इक्यासी", "बयासी", "तिरासी", "च्यासी", "पचासी", "छियासी", "सत्तासी", "अट्ठासी", "नवासी"),
     ("इक्यानवे", "बानवे", "तिरानवे", "चौरानवे", "पंचानवे", "छियानवे", "सत्तानवे", "अट्ठानवे", "निन्यानवे")),
    (("ikkees", "baees", "teeis", "chaubees", "pacchees", "chhabbees", "sattaees", "atthaees", "untees"),
     ("ikatees", "batees", "tainteis", "chaunteis", "painteis", "chhatais", "saintis", "attais", "untalees"),
     ("ikatalees", "bayaalees", "taintalees", "chavaalees", "paintalees", "chiyaalees", "saintalees", "attalees", "unchaas"),
     ("ikyavan", "baavan", "tirepan", "chavan", "penchavan", "chhappan", "sattavan", "atthavan", "unsath"),
     ("ikasath", "baasath", "iresath", "choosath", "paisath", "chhasath", "sainsath", "arsath", "unhattar"),
     ("ikahattar", "bahattar", "tihattar", "chihattar", "pahattar", "chihattar", "satahattar", "athahattar", "unyasi"),
     ("ikyasi", "bayasi", "tirasi", "chyasi", "pachasi", "cheyasi", "sattasi", "atthasi", "navasi"),
     ("ikyanve", "banve", "tiryanve", "chouryanve", "panchanve", "chhiyanve", "sattyanve", "athyanve", "ninyanve")),
)
_SCALES = (("सौ", "हज़ार", "लाख", "करोड़"), ("sau", "hazaar", "laakh", "crore"), ("hundred", "thousand", "lakh", "crore"))
_CURRENCY = (("रुपये", "पैसे"), ("rupaye", "paise"), ("rupees", "paise"))


def _below_hundred(number: int, language: int) -> str:
    if number < 10:
        return _UNITS[language][number - 1] if number else _ZERO[language]
    if number < 20:
        return _TEENS[language][number - 10]
    tens, unit = divmod(number, 10)
    if unit == 0:
        return _TENS[language][tens - 2]
    if language == 2:   # English composes: forty-five. Hindi does not: पैंतालीस.
        return f"{_TENS[2][tens - 2]}-{_UNITS[2][unit - 1]}"
    return _COMPOUND[language][tens - 2][unit - 1]


def _words(number: int, language: int) -> str:
    """Whole-number words using Indian grouping (crore / lakh / thousand / hundred)."""
    if number == 0:
        return _ZERO[language]
    parts = []
    crore = number // 1_00_00_000
    if crore:                      # the only group that can itself need grouping (250 crore)
        head = _below_hundred(crore, language) if crore < 100 else _words(crore, language)
        parts.append(f"{head} {_SCALES[language][3]}")
    for value, scale in ((number % 1_00_00_000 // 1_00_000, 2), (number % 1_00_000 // 1_000, 1),
                         (number % 1_000 // 100, 0)):
        if value:
            parts.append(f"{_below_hundred(value, language)} {_SCALES[language][scale]}")
    remainder = number % 100
    if remainder:
        parts.append(_below_hundred(remainder, language))
    return " ".join(parts)


def amount_words(amount_paise: int, language: str) -> str:
    """Spoken form of an amount in paise, e.g. 125050 -> "एक हज़ार दो सौ पचास रुपये पचास पैसे"."""
    index = {"hi": 0, "hinglish": 1, "en": 2}.get(language, 1)
    rupees, paise = divmod(abs(int(amount_paise)), 100)
    parts = []
    if rupees:
        parts.append(f"{_words(rupees, index)} {_CURRENCY[index][0]}")
    if paise:
        parts.append(f"{_below_hundred(paise, index)} {_CURRENCY[index][1]}")
    spoken = " ".join(parts) or (f"{_ZERO[index]} {_CURRENCY[index][0]}" if index != 2 else "zero rupees")
    if amount_paise < 0:
        spoken = ("माइनस " if index == 0 else "minus ") + spoken
    return spoken
