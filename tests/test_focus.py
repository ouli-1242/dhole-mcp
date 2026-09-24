"""Focus tests: BM25 content filtering.

Tests the real focus_content function against real text. No mocks.
Adversarial: empty query is no-op, single block is no-op, no matching terms
returns fallback blocks, heading context preserved.
"""

import re

from dhole_mcp.focus import (
    focus_content, _split_blocks, _tokens, _is_heading, _is_table, _is_code,
)


class TestFocusContent:

    def test_relevant_blocks_kept(self):
        text = (
            "Python is a programming language.\n\n"
            "Java is also a programming language.\n\n"
            "Rust is a systems programming language with memory safety.\n\n"
            "JavaScript runs in the browser."
        )
        result = focus_content(text, "Python programming")
        assert "Python is a programming language" in result
        # The header should mention focus
        assert "Focus:" in result

    def test_empty_query_returns_original(self):
        text = "Some content.\n\nMore content."
        assert focus_content(text, "") == text

    def test_none_query_returns_original(self):
        text = "Some content.\n\nMore content."
        assert focus_content(text, None) == text

    def test_single_block_returns_original(self):
        text = "Just one block of text."
        assert focus_content(text, "anything") == text

    def test_empty_text_returns_original(self):
        assert focus_content("", "query") == ""

    def test_no_matching_terms_returns_fallback_top(self):
        text = "Block A about cooking.\n\nBlock B about gardening.\n\nBlock C about painting."
        result = focus_content(text, "quantum physics")
        # Fallback: should return top blocks (not empty)
        assert "Block" in result
        assert "Focus:" in result

    def test_heading_preceding_kept_block_preserved(self):
        text = (
            "# Section Header\n\n"
            "This block talks about Python.\n\n"
            "# Other Header\n\n"
            "This block talks about Java."
        )
        result = focus_content(text, "Python")
        assert "Section Header" in result  # heading preserved for context
        assert "Python" in result

    def test_order_preserved(self):
        text = (
            "First mention of Python.\n\n"
            "Something about Java.\n\n"
            "Second mention of Python.\n\n"
            "Something about Go."
        )
        result = focus_content(text, "Python")
        first_pos = result.find("First mention")
        second_pos = result.find("Second mention")
        assert first_pos < second_pos

    def test_focus_header_includes_query_and_block_count(self):
        text = "Apple pie recipe.\n\nBanana bread recipe.\n\nCherry tart recipe.\n\nDate cake recipe."
        result = focus_content(text, "apple")
        assert "Focus:" in result
        assert "'apple'" in result


class TestSplitBlocks:

    def test_splits_on_blank_lines(self):
        blocks = _split_blocks("A\n\nB\n\nC")
        assert len(blocks) == 3

    def test_consecutive_blank_lines_dont_create_empty_blocks(self):
        blocks = _split_blocks("A\n\n\n\nB")
        assert len(blocks) == 2

    def test_no_blank_lines_returns_one_block(self):
        blocks = _split_blocks("A\nB\nC")
        assert len(blocks) == 1

    def test_empty_text_returns_empty_list(self):
        assert _split_blocks("") == []


class TestIsHeading:

    def test_markdown_h1(self):
        assert _is_heading("# Title") is True

    def test_markdown_h2(self):
        assert _is_heading("## Subtitle") is True

    def test_not_heading(self):
        assert _is_heading("Regular paragraph") is False

    def test_blank_then_heading(self):
        assert _is_heading("\n\n# Heading") is True


class TestTokens:

    def test_lowercases(self):
        assert "hello" in _tokens("Hello World")

    def test_filters_single_chars(self):
        tokens = _tokens("a b cd ef")
        assert "cd" in tokens
        assert "ef" in tokens
        assert "a" not in tokens
        assert "b" not in tokens

    def test_extracts_alphanumeric(self):
        tokens = _tokens("python3 async2026")
        assert "python3" in tokens
        assert "async2026" in tokens

    def test_empty_text(self):
        assert _tokens("") == []


# ─── Heading-aware BM25 + table/code preservation ──

class TestHeadingAwareBM25:

    def test_blocks_under_matching_heading_boosted(self):
        text = """# Introduction

This is an intro paragraph with no query terms.

## Model Architecture

The hidden dimension is critical for performance.
Each layer processes the full representation.

## Training Details

We used Adam optimizer with learning rate scheduling."""

        result = focus_content(text, "model architecture dimension")
        assert "Model Architecture" in result

    def test_heading_without_query_terms_not_boosted(self):
        text = """## Training Details

We used Adam optimizer with cosine annealing.

## Model Architecture

The architecture uses 96 transformer layers."""

        result = focus_content(text, "training details optimizer")
        assert "Adam optimizer" in result

    def test_heading_level_hierarchy(self):
        text = """## Architecture

### Transformer Architecture

The attention mechanism is key.

### RNN Architecture

Recurrent connections process sequences."""

        result = focus_content(text, "architecture transformer attention")
        assert "Transformer Architecture" in result
        assert "attention mechanism" in result

    def test_table_preservation(self):
        text = """# Introduction

Some intro text here.

| Model | d_model | layers |
|---|---|---|
| GPT-3 | 12288 | 96 |

## Other Section

Unrelated content about training."""

        result = focus_content(text, "GPT-3 d_model 12288")
        assert "12288" in result
        assert "|" in result  # table format preserved


class TestTableCodeDetection:

    def test_is_table_markdown(self):
        assert _is_table("| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |") is True

    def test_is_table_not_prose(self):
        assert _is_table("This is a paragraph of text with no pipes.") is False

    def test_is_code_fenced(self):
        assert _is_code("```python\ndef foo():\n    pass\n```") is True

    def test_is_code_indented(self):
        assert _is_code("    def foo():\n        return 42\n    bar = foo()") is True

    def test_is_code_not_prose(self):
        assert _is_code("This is a normal paragraph.") is False


# ─── Anchoring the cut to the best block ───────────────────────────────
#
# A SENTENCE query used to keep most of the page. BM25+ keeps idf > 0 for every
# term, so each of a question's ubiquitous words adds a small positive score to
# nearly every block and the sum clears the absolute threshold. Measured on
# docs.python.org/3/library/asyncio-task.html (88 blocks): 'TaskGroup' kept 9%
# of blocks while "What is the difference between asyncio.gather and
# asyncio.TaskGroup?" kept 76% — i.e. the documented usage (focus='question')
# was the case that saved the least context. The cut is therefore anchored to
# the best CONTENT block's score.
#
# The corpus below has GRADED relevance (the four query terms appear in 4, 2
# and 1 of 20 blocks), which is what a real page looks like. Uniform filler
# cannot reproduce the defect at all: with df == n the idf collapses to ~0.5/n,
# so even the absolute cut drops it — measured while writing this test.


class TestFocusIsAnchoredToTheBestBlock:

    QUERY = "TaskGroup cancel timeout shield"

    @classmethod
    def _graded_page(cls) -> str:
        return "\n\n".join(
            ["TaskGroup cancel timeout shield."]      # all four terms
            + ["TaskGroup cancel."] * 3               # two
            + ["TaskGroup."] * 6                      # one
            + ["Unrelated paragraph about something else."] * 10)

    @staticmethod
    def _kept(result: str) -> int:
        """The count the agent itself reads in the Focus header."""
        return int(re.search(r"showing (\d+) of (\d+) blocks", result).group(1))

    def test_a_sentence_query_no_longer_keeps_half_the_page(self):
        page = self._graded_page()
        absolute_only = focus_content(page, self.QUERY, relative_threshold=0.0)
        anchored = focus_content(page, self.QUERY)
        assert self._kept(absolute_only) == 10, "锚定前的行为（复现缺陷形状）"
        assert self._kept(anchored) == 4
        assert self._kept(anchored) < self._kept(absolute_only)

    def test_the_best_block_always_survives(self):
        """The cut must never remove the block that IS the answer."""
        for relative in (0.0, 0.33, 0.9):
            out = focus_content(self._graded_page(), self.QUERY,
                                relative_threshold=relative)
            assert "timeout shield" in out
            assert self._kept(out) >= 1

    def test_a_keyword_query_keeps_everything_it_kept_before(self):
        """No regression on the case that already worked: with identical
        relevant blocks the best score IS the threshold-clearing score, so the
        relative cut cannot drop any of them."""
        page = "\n\n".join(["TaskGroup timeout."] * 3
                           + ["Unrelated paragraph about something else."] * 10)
        for relative in (0.0, 0.33, 0.9):
            out = focus_content(page, "timeout", relative_threshold=relative)
            assert self._kept(out) == 3
            assert out.count("TaskGroup timeout.") == 3

    def test_the_absolute_threshold_is_still_a_floor(self):
        """A page whose best block is itself weak keeps the permissive
        behaviour — the relative cut may not turn a weak match into an empty
        page."""
        page = self._graded_page()
        everything = focus_content(page, self.QUERY, threshold=0.0,
                                   relative_threshold=0.0)
        assert self._kept(everything) == 20
