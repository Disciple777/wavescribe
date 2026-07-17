"""SRT (SubRip) subtitle generation from Whisper timed segments.

Converts a list of segment dicts (with start, end, text keys)
into a properly formatted .srt file content.
"""

import math
import re
from typing import Any, Dict, List, Optional


def _seconds_to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp format ``HH:MM:SS,mmm``.

    Args:
        seconds: Time in seconds (float).

    Returns:
        Formatted SRT timestamp string.
    """
    # Clamp negative values
    if seconds < 0:
        seconds = 0.0

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))

    # Handle rounding that pushes millis to 1000
    if millis >= 1000:
        millis -= 1000
        secs += 1
        if secs >= 60:
            secs -= 60
            minutes += 1
            if minutes >= 60:
                minutes -= 60
                hours += 1

    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


# Maximum characters per line for readability
_MAX_LINE_LENGTH = 42

# Maximum duration for a single subtitle block (seconds)
# Segments longer than this will be split
_MAX_SEGMENT_DURATION = 6.0

# Minimum duration for a subtitle (seconds)
_MIN_SEGMENT_DURATION = 1.0

# Default max words per subtitle block (0 = disabled, use existing char-based logic)
_DEFAULT_MAX_WORDS_PER_BLOCK = 0


def _clean_segment_text(text: str) -> str:
    """Clean and normalize segment text for subtitle display.

    - Strips leading/trailing whitespace
    - Removes leading/trailing punctuation artifacts
    - Capitalizes first letter
    - Removes extra spaces
    """
    if not text:
        return ""

    text = text.strip()

    # Remove leading/trailing punctuation that looks like artifacts
    text = re.sub(r'^[,\s;:.\'"]+', '', text)
    text = re.sub(r'[,\s;:.\'"]+$', '', text)

    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text)

    # Capitalize first letter
    if text:
        text = text[0].upper() + text[1:]

    return text.strip()


def _split_by_word_count(
    segment: Dict[str, Any],
    max_words: int,
) -> List[Dict[str, Any]]:
    """Split a segment into chunks with at most ``max_words`` words each.

    Timestamps are allocated proportionally based on word count.
    For example, if a 5-second segment has 10 words and max_words=3:
      - Chunk 1 (words 0-2):  0.0s → 1.5s  (30% of time)
      - Chunk 2 (words 3-5):  1.5s → 3.0s  (30%)
      - Chunk 3 (words 6-8):  3.0s → 4.5s  (30%)
      - Chunk 4 (words 9):    4.5s → 5.0s  (10%)

    Returns:
        List of new segment dicts with proportional timestamps.
    """
    text = segment["text"]
    words = text.split()
    total_words = len(words)

    if total_words <= max_words:
        return [segment]

    start = segment["start"]
    end = segment["end"]
    duration = end - start

    new_segments: List[Dict[str, Any]] = []
    current_start = start

    for chunk_idx in range(0, total_words, max_words):
        chunk_words = words[chunk_idx:chunk_idx + max_words]
        chunk_text = " ".join(chunk_words)
        chunk_word_count = len(chunk_words)

        # Proportion of total words in this chunk
        ratio = chunk_word_count / total_words
        chunk_duration = duration * ratio
        current_end = min(current_start + chunk_duration, end)

        new_segments.append({
            "id": segment["id"],
            "start": current_start,
            "end": current_end,
            "text": chunk_text,
        })
        current_start = current_end

    # If we ended before the original end (due to min duration padding), nudge the last chunk
    if new_segments and new_segments[-1]["end"] < end:
        new_segments[-1]["end"] = end

    return new_segments


def _split_long_segment(
    segment: Dict[str, Any],
    max_words: int = _DEFAULT_MAX_WORDS_PER_BLOCK,
) -> List[Dict[str, Any]]:
    """Split a long segment into smaller chunks for better readability.

    When ``max_words > 0``, splits purely by word count with proportional
    timestamps. Otherwise, splits at sentence boundaries or character
    midpoint as a fallback.

    Args:
        segment: A whisper segment dict with ``start``, ``end``, ``text``.
        max_words: Max words per subtitle block (0 = character-based split).

    Returns:
        List of new segment dicts with proportional timestamps.
    """
    text = segment["text"]
    start = segment["start"]
    end = segment["end"]
    duration = end - start

    # ── Word-count-based splitting ──
    if max_words > 0:
        return _split_by_word_count(segment, max_words)

    # ── Legacy character/duration-based splitting ──
    if duration <= _MAX_SEGMENT_DURATION and len(text) <= _MAX_LINE_LENGTH * 2:
        return [segment]

    # Try to split at sentence boundaries
    sentences = re.split(r'(?<=[.!?])\s+', text)

    if len(sentences) < 2:
        # No sentence boundary found; split at midpoint
        midpoint = len(text) // 2
        # Find a space near the midpoint
        best_pos = midpoint
        for offset in range(min(20, midpoint)):
            if text[midpoint - offset:midpoint - offset + 1] == ' ' and midpoint - offset > 0:
                best_pos = midpoint - offset
                break
            if text[midpoint + offset:midpoint + offset + 1] == ' ' and midpoint + offset < len(text):
                best_pos = midpoint + offset
                break

        part1 = text[:best_pos].strip()
        part2 = text[best_pos:].strip()

        if not part1 or not part2:
            return [segment]

        # Proportionally split time
        ratio = len(part1) / len(text) if len(text) > 0 else 0.5
        split_time = start + duration * ratio

        return [
            {"id": segment["id"], "start": start, "end": split_time, "text": part1},
            {"id": segment["id"], "start": split_time, "end": end, "text": part2},
        ]

    # Split at sentence boundaries, proportionally allocating time
    new_segments = []
    total_len = len(''.join(sentences)) or 1
    current_start = start

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        ratio = len(sentence) / total_len
        sentence_duration = duration * ratio
        # Ensure minimum visibility
        if sentence_duration < _MIN_SEGMENT_DURATION:
            sentence_duration = _MIN_SEGMENT_DURATION
        sentence_end = min(current_start + sentence_duration, end)

        new_segments.append({
            "id": segment["id"],
            "start": current_start,
            "end": sentence_end,
            "text": sentence,
        })
        current_start = sentence_end

    if not new_segments:
        return [segment]

    return new_segments


def _wrap_text_for_srt(
    text: str,
    max_words_per_line: int = _DEFAULT_MAX_WORDS_PER_BLOCK,
) -> str:
    """Wrap text into lines for SRT display.

    When ``max_words_per_line > 0``, wraps at that many words per line.
    Otherwise, wraps at ``_MAX_LINE_LENGTH`` characters per line.
    Returns text with line breaks (max 2 lines recommended for subtitles).
    """
    words = text.split()
    total_words = len(words)

    # ── Word-count-based wrapping ──
    if max_words_per_line > 0:
        if total_words <= max_words_per_line:
            return text
        lines: List[str] = []
        for i in range(0, total_words, max_words_per_line):
            lines.append(" ".join(words[i:i + max_words_per_line]))
        if len(lines) > 2:
            mid = len(lines) // 2
            lines = [" ".join(lines[:mid]), " ".join(lines[mid:])]
        return "\n".join(lines)

    # ── Legacy character-based wrapping ──
    if len(text) <= _MAX_LINE_LENGTH:
        return text

    lines = []
    current_line = ""

    for word in words:
        if len(current_line) + len(word) + 1 > _MAX_LINE_LENGTH:
            if current_line:
                lines.append(current_line.strip())
            current_line = word
        else:
            current_line += " " + word if current_line else word

    if current_line:
        lines.append(current_line.strip())

    # Keep at most 2 lines for subtitle readability
    if len(lines) > 2:
        mid = len(lines) // 2
        lines = [" ".join(lines[:mid]), " ".join(lines[mid:])]

    return "\n".join(lines)


def segments_to_srt(
    segments: List[Dict[str, Any]],
    split_long: bool = True,
    max_duration: float = _MAX_SEGMENT_DURATION,
    max_words_per_block: int = _DEFAULT_MAX_WORDS_PER_BLOCK,
) -> str:
    """Convert Whisper timed segments to SRT subtitle content.

    Each segment dict must have at least ``start`` (float), ``end`` (float),
    and ``text`` (str) keys. ``id`` is optional (auto-numbered if absent).

    Args:
        segments: List of segment dicts from Whisper.
        split_long: Whether to split long segments for readability.
        max_duration: Max seconds per subtitle block (default 6.0).
        max_words_per_block: Max words per subtitle block (0 = use
            character/duration-based splitting). Helps create very short
            subtitle blocks for single-line display.

    Returns:
        Complete SRT content as a string, including BOM for Unicode
        compatibility with video editors like CapCut.
    """
    if not segments:
        return ""

    # Optionally split long segments
    processed: List[Dict[str, Any]] = []
    if split_long:
        for seg in segments:
            processed.extend(_split_long_segment(seg, max_words=max_words_per_block))
    else:
        processed = list(segments)

    # ── Build clean entries with minimum duration ──
    entries: List[Dict[str, Any]] = []
    for seg in processed:
        text = _clean_segment_text(seg.get("text", ""))
        if not text:
            continue

        start = float(seg["start"])
        end = float(seg["end"])

        # Ensure minimum duration for readability
        if end - start < _MIN_SEGMENT_DURATION:
            end = start + _MIN_SEGMENT_DURATION

        entries.append({"start": start, "end": end, "text": text})

    # ── Fix overlaps ──
    # Minimum duration padding can push a subtitle's end past the next
    # subtitle's start (especially when word-count splitting creates
    # short chunks from adjacent Whisper segments). Clamp each end to
    # the next start with a 1 ms gap so players never show overlapping text.
    for i in range(len(entries) - 1):
        if entries[i]["end"] > entries[i + 1]["start"]:
            # Clamp to next start minus 1ms, but never below our own start
            entries[i]["end"] = max(
                entries[i]["start"],
                entries[i + 1]["start"] - 0.001,
            )

    # ── Build SRT content ──
    srt_lines = []
    # Add UTF-8 BOM for compatibility with Windows editors
    srt_lines.append("\ufeff")

    for i, entry in enumerate(entries, 1):
        wrapped_text = _wrap_text_for_srt(entry["text"], max_words_per_line=max_words_per_block)

        srt_lines.append(
            f"{i}\n"
            f"{_seconds_to_srt_time(entry['start'])} --> {_seconds_to_srt_time(entry['end'])}\n"
            f"{wrapped_text}\n"
        )

    return "\n".join(srt_lines)


def segments_to_vtt(
    segments: List[Dict[str, Any]],
    split_long: bool = True,
    max_words_per_block: int = _DEFAULT_MAX_WORDS_PER_BLOCK,
) -> str:
    """Convert Whisper timed segments to WebVTT subtitle content.

    Similar to SRT but with VTT header and slightly different format:
      - Uses ``.`` instead of ``,`` for milliseconds
      - Header required: ``WEBVTT``
      - No segment numbering
    """
    if not segments:
        return ""

    srt_content = segments_to_srt(
        segments, split_long=split_long, max_words_per_block=max_words_per_block,
    )

    # Replace SRT format with VTT
    lines = srt_content.split("\n")
    vtt_lines = ["WEBVTT\n"]

    for line in lines:
        # Remove UTF-8 BOM if present
        line = line.replace("\ufeff", "")
        # Skip segment numbers (VTT doesn't use them)
        if line.strip().isdigit():
            continue
        # Convert SRT timestamp to VTT (comma → dot)
        if "-->" in line:
            line = line.replace(",", ".")
        vtt_lines.append(line)

    return "\n".join(vtt_lines)


def estimate_subtitle_count(duration_seconds: float) -> int:
    """Estimate how many subtitles a video of the given duration will have.

    Rough estimate: one subtitle every 4-6 seconds.
    """
    return max(1, int(duration_seconds / 5))
