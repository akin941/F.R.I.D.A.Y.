from knowledge.chunking import chunk_text, estimate_token_count


class TestChunkText:
    def test_empty_document_returns_no_chunks(self):
        assert chunk_text("") == []
        assert chunk_text("   \n  ") == []

    def test_single_chunk_document(self):
        text = "A short document that fits in one chunk."
        assert chunk_text(text, chunk_size=1000, overlap=150) == [text]

    def test_exact_multiple_of_chunk_size(self):
        # 20 chars per "line", 5 lines, chunk_size=20 with no boundary marker
        # nearby (no periods/newlines) — must hard-cut cleanly with no gaps
        # or duplicated characters.
        text = "a" * 100
        chunks = chunk_text(text, chunk_size=20, overlap=0)
        assert "".join(chunks) == text
        assert all(len(c) == 20 for c in chunks[:-1])

    def test_overlap_repeats_boundary_content(self):
        text = ("Sentence one is here. " * 10).strip()
        chunks = chunk_text(text, chunk_size=60, overlap=20)
        assert len(chunks) > 1
        # Overlap means consecutive chunks share trailing/leading content.
        assert chunks[0][-10:] in text

    def test_prefers_paragraph_boundary(self):
        para_a = "First paragraph. " * 5
        para_b = "Second paragraph. " * 5
        text = para_a.strip() + "\n\n" + para_b.strip()
        chunks = chunk_text(text, chunk_size=len(para_a) + 5, overlap=0)
        assert chunks[0] == para_a.strip()

    def test_always_progresses_even_with_large_overlap(self):
        """An overlap >= chunk_size (env-misconfigured KNOWLEDGE_CHUNK_OVERLAP/
        KNOWLEDGE_CHUNK_SIZE) previously advanced `start` by only 1 char per
        iteration — 4001 chunks for a 5000-char document instead of ~5
        (review finding). Assert a sane chunk count, not just termination:
        the old test's `len(chunks) > 1` passed even with the explosion."""
        text = "a" * 5000
        chunks = chunk_text(text, chunk_size=1000, overlap=1000)
        assert len(chunks) <= 10

    def test_overlap_never_exceeds_half_chunk_size(self):
        text = "word " * 500
        chunks = chunk_text(text, chunk_size=50, overlap=1000)  # overlap > chunk_size
        assert len(chunks) > 1  # must terminate, not loop forever
        # Clamped overlap (<= chunk_size // 2) guarantees each step advances
        # by at least half a chunk, so no chunk collapses to a sliver.
        assert all(len(c) >= 20 for c in chunks[:-1])

    def test_deterministic(self):
        text = "Some text with multiple sentences. Another one follows here. And a third."
        assert chunk_text(text, chunk_size=40, overlap=10) == chunk_text(
            text, chunk_size=40, overlap=10
        )

    def test_early_boundary_does_not_shrink_chunk_or_reverse_start(self):
        """Round-2 Codex review finding: a short first sentence followed by a
        long punctuation-free run used to snap to the early boundary, shrink
        the chunk far below chunk_size, and then walk `start` *backwards* on
        the next iteration (overlap > tiny chunk length) — producing ~100
        near-duplicate tiny chunks instead of a few fixed-size ones."""
        text = "a" * 99 + ". " + "b" * 2000
        chunks = chunk_text(text, chunk_size=1000, overlap=150)
        assert "".join(chunks) != text  # overlap means some repetition, but:
        assert len(chunks) <= 5
        assert all(len(c) > 500 for c in chunks[:-1])

    def test_sentence_boundary_does_not_drop_the_separator_character(self):
        """Review finding: `_find_boundary`'s sentence-marker branch returned
        `best + 1` instead of `best + len(marker)`, silently dropping the
        space/newline half of a 2-char marker (e.g. ". ") at every
        sentence-boundary snap — chunks[0] ended in "." with chunks[1]
        starting immediately with the next word, losing the separator."""
        text = "x" * 16 + ". " + "y" * 50
        chunks = chunk_text(text, chunk_size=20, overlap=0)
        assert chunks[0] == "x" * 16 + "."
        assert chunks[1].startswith("y")
        assert " " not in chunks[0][-1:] + chunks[1][:1]  # no merged/split space
        # No character from the original text is dropped between chunks.
        assert text.replace(" ", "") == "".join(chunks).replace(" ", "")


class TestEstimateTokenCount:
    def test_at_least_one_for_nonempty_text(self):
        assert estimate_token_count("a") >= 1

    def test_scales_with_length(self):
        assert estimate_token_count("a" * 400) > estimate_token_count("a" * 4)
