"""Unit tests for the smart formatting module.

Tests punctuation mapping, spacing cleanup, and auto-capitalization.
"""

import pytest

from app.formatter import (
    apply_punctuation,
    clean_spacing,
    auto_capitalize,
    format_transcription,
    cleanup_redundant_punctuation,
    convert_numbers,
)


class TestApplyPunctuation:
    """Tests for the punctuation mapping function."""

    def test_basic_punctuation(self):
        """Test basic punctuation commands become symbols."""
        assert apply_punctuation("hello period") == "hello ."
        assert apply_punctuation("hello comma") == "hello ,"
        assert apply_punctuation("question mark") == "?"
        assert apply_punctuation("exclamation mark") == "!"

    def test_case_insensitive(self):
        """Test that punctuation commands are matched case-insensitively."""
        assert apply_punctuation("Hello PERIOD world") == "Hello . world"
        assert apply_punctuation("OPEN PAREN test CLOSE PAREN") == "( test )"

    def test_brackets_and_braces(self):
        """Test bracket and brace mappings."""
        assert apply_punctuation("open bracket close bracket") == "[ ]"
        assert apply_punctuation("open brace close brace") == "{ }"
        assert apply_punctuation("open parenthesis close parenthesis") == "( )"

    def test_quotes(self):
        """Test quote mappings."""
        assert apply_punctuation("open quote hello close quote") == '" hello "'
        assert apply_punctuation("single quote test") == "' test"

    def test_symbols(self):
        """Test symbol mappings."""
        assert apply_punctuation("at sign") == "@"
        assert apply_punctuation("dollar sign") == "$"
        assert apply_punctuation("percent sign") == "%"
        assert apply_punctuation("asterisk") == "*"
        assert apply_punctuation("ampersand") == "&"

    def test_new_paragraph_and_line(self):
        """Test new paragraph and new line mappings.

        Note: apply_punctuation only replaces the command text, it does not
        clean up the surrounding spaces. That's handled by clean_spacing.
        """
        assert apply_punctuation("line one new paragraph line two") == "line one \n\n line two"
        assert apply_punctuation("line one new line line two") == "line one \n line two"

    def test_longest_match_first(self):
        """Test that longer patterns are matched before shorter ones."""
        # "open parenthesis" should match before "open paren"
        result = apply_punctuation("open parenthesis")
        assert "(" in result
        # "exclamation mark" should be fully matched
        result = apply_punctuation("exclamation mark")
        assert "!" in result

    def test_ellipsis(self):
        """Test ellipsis mapping."""
        assert apply_punctuation("dot dot dot") == "..."
        assert apply_punctuation("ellipsis") == "..."

    def test_no_matches(self):
        """Test that text with no punctuation commands is unchanged."""
        assert apply_punctuation("hello world") == "hello world"
        assert apply_punctuation("") == ""


class TestCleanSpacing:
    """Tests for the spacing cleanup function."""

    def test_remove_space_before_period(self):
        """Test that space before period is removed."""
        assert clean_spacing("hello .") == "hello."
        assert clean_spacing("end .") == "end."

    def test_remove_space_before_comma(self):
        """Test that space before comma is removed."""
        assert clean_spacing("hello , world") == "hello, world"

    def test_remove_space_before_question(self):
        """Test that space before question mark is removed."""
        assert clean_spacing("what ?") == "what?"

    def test_remove_space_before_exclamation(self):
        """Test that space before exclamation mark is removed."""
        assert clean_spacing("wow !") == "wow!"

    def test_remove_space_after_open_paren(self):
        """Test that space after open paren is removed."""
        assert clean_spacing("( hello") == "(hello"

    def test_multiple_spaces_collapsed(self):
        """Test that multiple spaces are collapsed to one."""
        assert clean_spacing("hello    world") == "hello world"

    def test_preserve_newlines(self):
        """Test that newlines are preserved and extra spacing around them is cleaned."""
        result = clean_spacing("hello   \n   world")
        assert result == "hello\nworld"

    def test_clean_spacing_around_newlines(self):
        """Test spacing cleanup around new paragraph markers."""
        result = clean_spacing("line one \n\n line two")
        assert result == "line one\n\nline two"

    def test_empty_string(self):
        """Test that empty string returns empty string."""
        assert clean_spacing("") == ""


class TestAutoCapitalize:
    """Tests for the auto-capitalization function."""

    def test_first_letter_capitalized(self):
        """Test that the first letter is capitalized."""
        assert auto_capitalize("hello world") == "Hello world"

    def test_after_period(self):
        """Test that text after a period is capitalized."""
        result = auto_capitalize("hello. world")
        assert result == "Hello. World"

    def test_after_question_mark(self):
        """Test that text after a question mark is capitalized."""
        result = auto_capitalize("what? next")
        assert result == "What? Next"

    def test_after_exclamation(self):
        """Test that text after an exclamation mark is capitalized."""
        result = auto_capitalize("wow! amazing")
        assert result == "Wow! Amazing"

    def test_empty_string(self):
        """Test that empty string returns empty string."""
        assert auto_capitalize("") == ""

    def test_already_capitalized(self):
        """Test that already capitalized text remains capitalized."""
        assert auto_capitalize("Hello world") == "Hello world"

    def test_multiple_sentences(self):
        """Test multiple sentences with various punctuation."""
        result = auto_capitalize("hello. how are you? i am fine!")
        assert result == "Hello. How are you? I am fine!"

    def test_newline_not_capitalized(self):
        """Test that text after a newline (without preceding .?!) is NOT capitalized.

        Auto-capitalization only triggers after sentence-ending punctuation (. ? !),
        not after bare newlines.
        """
        result = auto_capitalize("hello\nworld")
        assert result == "Hello\nworld"


class TestFormatTranscription:
    """Integration tests for the full formatting pipeline."""

    def test_full_formatting(self):
        """Test full formatting pipeline with punctuation, spacing, and caps."""
        result = format_transcription("hello period this is a test comma it works great exclamation mark")
        assert "Hello" in result
        assert "." in result
        assert "," in result
        assert "!" in result

    def test_spoken_sentence(self):
        """Test a realistic spoken sentence."""
        input_text = "open parenthesis hello close parenthesis period"
        result = format_transcription(input_text)
        assert "(" in result
        assert ")" in result
        assert "." in result

    def test_new_paragraph_formatting(self):
        """Test new paragraph with formatting."""
        input_text = "first paragraph new paragraph second paragraph"
        result = format_transcription(input_text)
        assert "\n\n" in result

    def test_new_paragraph_full_pipeline(self):
        """Test the full pipeline correctly handles new paragraph spacing.

        Note: auto_capitalize only capitalizes after .?! punctuation, not after
        bare newlines, so the second paragraph starts lowercase.
        """
        result = format_transcription("first paragraph new paragraph second paragraph")
        parts = result.split("\n\n")
        assert len(parts) == 2
        assert parts[0].strip() == "First paragraph"
        assert parts[1].strip() == "second paragraph"

    def test_new_paragraph_with_period(self):
        """Test new paragraph with preceding period triggers capitalization."""
        result = format_transcription("first sentence period new paragraph second sentence period")
        assert "First sentence." in result
        assert "\n\n" in result
        assert "Second sentence." in result

    def test_empty_string(self):
        """Test that empty string returns empty string."""
        assert format_transcription("") == ""

    def test_whitespace_only(self):
        """Test that whitespace-only string returns empty string."""
        assert format_transcription("   ") == ""

    def test_no_op_input(self):
        """Test that text with no punctuation commands passes through."""
        result = format_transcription("this is a test with no commands")
        assert result == "This is a test with no commands"


class TestCleanupRedundantPunctuation:
    """Tests for the redundant punctuation cleanup function.

    Covers commas around . ! ? : ;, repeated commas, double periods,
    and ellipsis preservation.
    """

    # ── Commas around . ! ? (existing) ──

    def test_comma_before_period(self):
        """,. -> . (comma before period)."""
        assert cleanup_redundant_punctuation("down,.") == "down."

    def test_comma_before_exclamation(self):
        """,! -> ! (comma before exclamation)."""
        assert cleanup_redundant_punctuation("it,!") == "it!"

    def test_comma_after_period(self):
        """., -> . (comma after period)."""
        assert cleanup_redundant_punctuation("down.,") == "down."

    def test_comma_before_and_after_period(self):
        """,., -> . (comma-period-comma collapses)."""
        assert cleanup_redundant_punctuation("down,.,") == "down."

    def test_comma_excl_period(self):
        """Comma+exclamation+period keeps exclamation + period."""
        assert cleanup_redundant_punctuation("it,!.") == "it!."

    # ── Commas around ; and : (new) ──

    def test_comma_before_semicolon(self):
        """,; -> ; (comma before semicolon)."""
        assert cleanup_redundant_punctuation("list,;") == "list;"

    def test_comma_after_semicolon(self):
        """;, -> ; (comma after semicolon)."""
        assert cleanup_redundant_punctuation("list;,") == "list;"

    def test_semicolon_with_commas_both_sides(self):
        """,;, -> ; (commas both sides of semicolon)."""
        assert cleanup_redundant_punctuation("list,;,") == "list;"

    def test_comma_before_colon(self):
        """,: -> : (comma before colon)."""
        assert cleanup_redundant_punctuation("list,:") == "list:"

    def test_comma_after_colon(self):
        """:, -> : (comma after colon)."""
        assert cleanup_redundant_punctuation("list:,") == "list:"

    def test_colon_with_commas_both_sides(self):
        """,:, -> : (commas both sides of colon)."""
        assert cleanup_redundant_punctuation("list,:,") == "list:"

    # ── Repeated commas (new) ──

    def test_double_comma(self):
        """,, -> , (double comma collapses)."""
        assert cleanup_redundant_punctuation("vegetables,,") == "vegetables,"

    def test_triple_comma(self):
        """,,, -> , (triple comma collapses)."""
        assert cleanup_redundant_punctuation("lemon,,,") == "lemon,"

    def test_quadruple_comma(self):
        """,,,, -> , (quadruple comma collapses)."""
        assert cleanup_redundant_punctuation("garlic,,,,") == "garlic,"

    # ── Combined patterns (new) ──

    def test_comma_before_period_after_collapse(self):
        """,,.\", -> . (double comma before period collapses both)."""
        # Phase 1: ",," -> "," -> ",."
        # Phase 2: comma before . -> removed -> "."
        assert cleanup_redundant_punctuation("down,,.") == "down."

    def test_comma_then_double_period(self):
        """Comma then double period collapses to single period."""
        # Phase 1: no consecutive commas
        # Phase 2: comma before . -> removed -> ".."
        # Phase 3: double period -> single period
        assert cleanup_redundant_punctuation("5,..") == "5."

    # ── Double periods and ellipsis (existing) ──

    def test_double_period(self):
        """Double period becomes single."""
        assert cleanup_redundant_punctuation("so..") == "so."

    def test_ellipsis_preserved(self):
        """Ellipsis (three dots) should be preserved, not collapsed."""
        assert cleanup_redundant_punctuation("...") == "..."
        assert cleanup_redundant_punctuation("word...") == "word..."

    # ── User's real-world examples ──

    def test_shopping_list_example(self):
        """User's exact shopping-list example 2.1."""
        input_text = "This is our shopping list;, vegetables,, eggplant,, garlic,, a cart full of oranges, and one scoop of salt."
        expected = "This is our shopping list; vegetables, eggplant, garlic, a cart full of oranges, and one scoop of salt."
        assert cleanup_redundant_punctuation(input_text) == expected

    def test_shopping_list_example_two(self):
        """User's exact shopping-list example 2.2."""
        input_text = "This is our shopping list,:, lemon,,, eggplant,,, garlic,,, a cart full of oranges."
        expected = "This is our shopping list: lemon, eggplant, garlic, a cart full of oranges."
        assert cleanup_redundant_punctuation(input_text) == expected

    def test_well_formatted_unchanged(self):
        """Well-formatted text should pass through unchanged."""
        text = "So right now I'm trying the auto speech recognition."
        assert cleanup_redundant_punctuation(text) == text

    # ── Edge cases ──

    def test_no_punctuation(self):
        """Text without punctuation is unchanged."""
        assert cleanup_redundant_punctuation("hello world") == "hello world"

    def test_empty_string(self):
        """Empty string returns empty string."""
        assert cleanup_redundant_punctuation("") == ""


class TestConvertNumbers:
    """Tests for the number conversion function."""

    def test_simple_numbers(self):
        """Basic number words become digits, adjacent ones merge."""
        # "one two three" -> "1 2 3" -> adjacent merge -> "12 3"
        assert convert_numbers("one two three") == "12 3"

    def test_ten_to_nineteen(self):
        """Teens are converted, adjacent ones merge."""
        # "ten eleven twelve" -> "10 11 12" -> adjacent merge -> "1011 12"
        assert convert_numbers("ten eleven twelve") == "1011 12"

    def test_tens(self):
        """Multiples of ten are converted and adjacent ones merge."""
        # "twenty thirty forty" -> "20 30 40" -> adjacent merge -> "2030 40"
        assert convert_numbers("twenty thirty forty") == "2030 40"

    def test_compound_numbers(self):
        """Compound numbers like twenty three merge via concatenation."""
        # "twenty three" -> "20 3" -> adjacent merge -> "203"
        assert convert_numbers("twenty three") == "203"

    def test_no_number_words(self):
        """Text without number words is unchanged."""
        assert convert_numbers("hello world") == "hello world"

    def test_sentence_with_period(self):
        """Test a full sentence ending with a period command."""
        result = format_transcription("this is a test period")
        assert result == "This is a test."
