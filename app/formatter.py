"""Smart formatting for transcribed speech.

Ports the Rust punctuation-mapping logic to Python:
  - ~40 spoken punctuation commands → symbols
  - Spacing cleanup (remove space before ,.?!:; etc.)
  - Auto-capitalization after . ? !
  - Bullet point support
"""

import re
from typing import Dict

# ── Punctuation Mappings ──

# Maps spoken commands (lowercase) to their punctuation symbols.
# Ordered by length (longest first) to prevent partial matches.
PUNCTUATION_MAP: Dict[str, str] = {
    # Brackets & braces
    "open parenthesis": "(",
    "close parenthesis": ")",
    "open paren": "(",
    "close paren": ")",
    "open bracket": "[",
    "close bracket": "]",
    "open brace": "{",
    "close brace": "}",
    "open curly brace": "{",
    "close curly brace": "}",
    "left paren": "(",
    "right paren": ")",
    "left bracket": "[",
    "right bracket": "]",
    "left brace": "{",
    "right brace": "}",
    # Quotes
    "open quote": "\"",
    "close quote": "\"",
    "single quote": "'",
    "double quote": "\"",
    "end quote": "\"",
    # Punctuation marks
    "period": ".",
    "comma": ",",
    "exclamation mark": "!",
    "exclamation point": "!",
    "question mark": "?",
    "colon": ":",
    "semicolon": ";",
    "apostrophe": "'",
    "hyphen": "-",
    "dash": "—",
    "em dash": "—",
    "en dash": "–",
    "underscore": "_",
    "ellipsis": "...",
    "dot dot dot": "...",
    # Symbols
    "asterisk": "*",
    "at sign": "@",
    "pound sign": "#",
    "hash sign": "#",
    "number sign": "#",
    "dollar sign": "$",
    "percent sign": "%",
    "caret": "^",
    "ampersand": "&",
    "and sign": "&",
    "pipe": "|",
    "vertical bar": "|",
    "tilde": "~",
    "backslash": "\\",
    "forward slash": "/",
    "slash": "/",
    "plus sign": "+",
    "minus sign": "-",
    "equals sign": "=",
    "greater than": ">",
    "less than": "<",
    "bullet point": "•",
    "bullet": "•",
    # New paragraph / new line
    "new paragraph": "\n\n",
    "new line": "\n",
    "newline": "\n",
}

# Compile a regex pattern that matches any of the punctuation commands
# as whole words (case-insensitive).
_PUNCTUATION_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(key) for key in PUNCTUATION_MAP) + r")\b",
    re.IGNORECASE,
)

# ── Spacing & Capitalization Rules ──

# Characters that should NOT have a space before them
_NO_SPACE_BEFORE = set(",.?!:;)]}»")

# Characters that should NOT have a space after them
_NO_SPACE_AFTER = set("([{«")

# Sentence-ending punctuation for auto-capitalization
_SENTENCE_END = re.compile(r'(?<=[.?!])\s+')


# ── Sentence-ending punctuation ──

# Characters that qualify as sentence-ending punctuation
_SENTENCE_ENDING = re.compile(r'[.?!…:;]$')


def auto_add_sentence_punctuation(text: str) -> str:
    """Add sentence-ending punctuation to text that has none.

    When ``punctuate_speech`` is OFF (no spoken punctuation commands),
    the transcription model may still output text without any punctuation
    marks (especially the local Whisper model). This function ensures
    every line of the text ends with a proper sentence-ending mark.

    - If the text doesn't already end with ``. ? ! … : ;``, adds a period.
    - Handles multi-line text, adding periods to lines that lack ending
      punctuation.
    - Preserves existing punctuation and formatting.

    Args:
        text: The transcribed text, possibly without ending punctuation.

    Returns:
        Text with sentence-ending punctuation added where missing.
    """
    if not text:
        return text

    # Normalize line endings
    lines = text.split('\n')
    result: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        if stripped and not _SENTENCE_ENDING.search(stripped):
            stripped += '.'
        result.append(stripped)

    return '\n'.join(result)


# ── Number Words ──

# Maps spoken number words to their digit equivalents.
# Handles 0-99 and some common larger terms.
_NUMBER_MAP: Dict[str, str] = {
    # 0-19
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19",
    # Tens
    "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50",
    "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
    # Large
    "hundred": "00", "thousand": "000", "million": "000000",
}

# Sort by length descending so longer phrases match first (e.g. "sixty six" before just "six")
_NUMBER_WORDS = sorted(_NUMBER_MAP.keys(), key=len, reverse=True)
# Pattern matches number words as whole words (case-insensitive)
_NUMBER_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _NUMBER_WORDS) + r")\b",
    re.IGNORECASE,
)


def cleanup_redundant_punctuation(text: str) -> str:
    """Clean up redundant adjacent punctuation marks.

    When the user speaks punctuation words ("comma", "period", "semicolon")
    and Whisper also adds its own punctuation (based on pauses), we can end
    up with messy adjacent marks like ",;", ",:", ",,,", ",.:", etc.
    This function collapses these, keeping the punctuation that came last
    (typically the strongest/ most intentional one).

    Phase 1 — Collapse repeated commas (e.g. ",," → ",", ",,," → ",")
    Phase 2 — Remove commas before/after stronger punctuation (: ; . ! ?)
    Phase 3 — Collapse double periods into one, preserving ellipsis "..."

    Examples:
        "word,,"     → "word,"
        "word,;"     → "word;"
        "word;,"     → "word;"
        "word,:"     → "word:"
        "word:,"     → "word:"
        "word,."     → "word."
        "word.,"     → "word."
        "word,.,"    → "word."
        "word,."     → "word."
        "word,.."    → "word."
        "word...,"   → "word..."  (ellipsis preserved)
    """
    if not text:
        return text

    # Phase 1: Collapse consecutive commas (",," → ",", ",,," → ",", etc.)
    text = re.sub(r",{2,}", ",", text)

    # Phase 2: Remove commas immediately before/after stronger punctuation
    # Remove comma before : ; . ! ?
    text = re.sub(r",(?=[:;.!?])", "", text)
    # Remove comma after : ; . ! ?
    text = re.sub(r"(?<=[:;.!?]),", "", text)

    # Phase 3: Collapse double periods into one, preserving ellipsis "..."
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)

    return text


def convert_numbers(text: str) -> str:
    """Convert spoken number words to their digit forms.

    Handles simple cases ("one" → "1", "twenty three" → "20 3" → "23").
    For compound numbers like "twenty three", adjacent tens + unit words
    are merged after conversion (e.g., "20 3" → "23").

    Does not handle all edge cases (e.g. "one hundred and five"),
    but covers the most common use cases.
    """
    if not text:
        return text

    def _replace_num(m: re.Match) -> str:
        word = m.group(1).lower()
        return _NUMBER_MAP.get(word, m.group(0))

    result = _NUMBER_PATTERN.sub(_replace_num, text)

    # Merge adjacent digit groups separated by a single space,
    # e.g. "20 3" → "23", "4 000 000" → "4000000"
    result = re.sub(r"(\d+)\s+(\d+)", lambda m: m.group(1) + m.group(2), result)

    return result


def apply_punctuation(text: str) -> str:
    """Replace spoken punctuation commands with actual symbols.

    Case-insensitive matching, longest-first to avoid partial matches.
    """
    def _replace_match(m: re.Match) -> str:
        key = m.group(1).lower()
        return PUNCTUATION_MAP.get(key, m.group(0))
    return _PUNCTUATION_PATTERN.sub(_replace_match, text)


def clean_spacing(text: str) -> str:
    """Remove incorrect whitespace around punctuation.

    - Remove space before , . ? ! : ; ) ] } »
    - Remove space after ( [ { «
    - Collapse multiple spaces into one.
    - Strip leading/trailing whitespace.
    """
    result = text
    # Remove space before punctuation symbols
    for ch in _NO_SPACE_BEFORE:
        result = result.replace(f" {ch}", ch)
    # Remove space after opening symbols
    for ch in _NO_SPACE_AFTER:
        result = result.replace(f"{ch} ", ch)
    # Collapse multiple spaces (but preserve newlines)
    result = re.sub(r"[ \t]{2,}", " ", result)
    # Remove space before newlines
    result = re.sub(r"[ \t]+\n", "\n", result)
    # Remove space after newlines
    result = re.sub(r"\n[ \t]+", "\n", result)
    # Strip
    result = result.strip()
    return result


def auto_capitalize(text: str) -> str:
    """Capitalize the first letter of the text and after sentence-ending punctuation.

    Preserves existing whitespace (including newlines) around sentences.
    """
    if not text:
        return text

    result = text
    # Capitalize first character
    result = result[0].upper() + result[1:]

    # Capitalize the first letter after sentence-ending punctuation + whitespace
    # Uses a callback to preserve the original whitespace (including newlines).
    def _cap_next(m: re.Match) -> str:
        # m.group(0) is the punctuation + whitespace, m.group(1) is the next char
        return m.group(0).upper()

    # Match .?! followed by any whitespace, then a word character
    result = re.sub(
        r'([.?!])(\s+)([a-zA-Z])',
        lambda m: m.group(1) + m.group(2) + m.group(3).upper(),
        result,
    )

    return result


def format_transcription(text: str) -> str:
    """Full formatting pipeline for transcribed text.

    1. Apply punctuation mappings
    2. Clean spacing
    3. Auto-capitalize
    """
    if not text or not text.strip():
        return ""

    result = apply_punctuation(text)
    result = clean_spacing(result)
    result = auto_capitalize(result)
    return result
