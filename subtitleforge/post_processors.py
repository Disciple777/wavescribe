"""Post-processing transformations for subtitle text.

These functions modify subtitle text after transcription but before
SRT/VTT export. They are applied as a pipeline so toggling a setting
re-processes the original transcription in place.

Pipeline order (deterministic):
  1. Remove punctuation
  2. Replace accented vowels (áéíóú → aeiou)
  3. Replace ñ → n
  4. ALL CAPS or all lowercase (mutually exclusive)
"""

import re
import unicodedata
from typing import Any, Dict, List


def apply_caps(text: str) -> str:
    """Convert text to ALL CAPS.

    Args:
        text: Input string.

    Returns:
        Uppercased string.
    """
    return text.upper()


def apply_lowercase(text: str) -> str:
    """Convert text to all lowercase.

    Args:
        text: Input string.

    Returns:
        Lowercased string.
    """
    return text.lower()


def replace_accents(text: str) -> str:
    """Replace accented vowel characters with their plain ASCII equivalents.

    Handles both uppercase and lowercase:
      áéíóú → aeiou
      ÁÉÍÓÚ → AEIOU

    Uses ``str.translate()`` for speed — O(n) with no regex overhead.

    Args:
        text: Input string possibly containing accented vowels.

    Returns:
        String with accented vowels replaced.
    """
    _ACCENT_MAP = str.maketrans({
        'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
        'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U',
    })
    return text.translate(_ACCENT_MAP)


def replace_enne(text: str) -> str:
    """Replace ñ with n and Ñ with N.

    Args:
        text: Input string possibly containing ñ/Ñ.

    Returns:
        String with ñ/Ñ replaced by n/N.
    """
    _ENNE_MAP = str.maketrans({'ñ': 'n', 'Ñ': 'N'})
    return text.translate(_ENNE_MAP)


def remove_punctuation(text: str) -> str:
    """Remove punctuation characters from subtitle text.

    Keeps letters (including accented), digits, spaces, and newlines.
    Removes everything else: ``. , ! ? ; : " ' — … ( ) [ ] { }``, etc.

    Args:
        text: Input string.

    Returns:
            String with punctuation stripped.
    """
    # \w with re.UNICODE keeps letters (including accented), digits, underscore
    # \s keeps spaces, tabs, newlines
    # Remove underscore from \w since it's punctuation-like
    return re.sub(r'[^\w\s]', '', text, flags=re.UNICODE).replace('_', ' ')


def apply_postprocessing_pipeline(
    segments: List[Dict[str, Any]],
    caps: bool = False,
    lowercase: bool = False,
    replace_accents_enabled: bool = False,
    replace_enne_enabled: bool = False,
    remove_punct: bool = False,
) -> List[Dict[str, Any]]:
    """Apply the selected post-processing transformations to all segments.

    Each segment's ``text`` key is transformed in-place.  The original
    ``text`` is **not** preserved — call this on a *copy* of the raw
    transcription data, or store raw segments separately and re-pipeline
    whenever toggles change.

    Pipeline order (applied sequentially):
      1. Remove punctuation  (``remove_punct``)
      2. Replace accented vowels (``replace_accents_enabled``)
      3. Replace ñ → n        (``replace_enne_enabled``)
      4. ALL CAPS or lowercase (mutually exclusive — *caps wins if both*)

    Args:
        segments: List of segment dicts with at least a ``text`` key.
        caps: Convert text to ALL CAPS.
        lowercase: Convert text to all lowercase.
        replace_accents_enabled: Replace accented vowels.
        replace_enne_enabled: Replace ñ → n.
        remove_punct: Remove punctuation characters.

    Returns:
        New list of segment dicts with transformed text.  Original list
        is not mutated.
    """
    result: List[Dict[str, Any]] = []
    for seg in segments:
        text = seg.get("text", "")

        # 1. Remove punctuation first so subsequent operations
        #    see clean text (avoids edge cases like "¡hola!")
        if remove_punct:
            text = remove_punctuation(text)

        # 2. Replace accented vowels
        if replace_accents_enabled:
            text = replace_accents(text)

        # 3. Replace ñ → n
        if replace_enne_enabled:
            text = replace_enne(text)

        # 4. Caps / lowercase (mutually exclusive — caps wins)
        if caps:
            text = apply_caps(text)
        elif lowercase:
            text = apply_lowercase(text)

        new_seg = dict(seg)
        new_seg["text"] = text
        result.append(new_seg)

    return result
