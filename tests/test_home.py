from __future__ import annotations

from locoder.tui.home import LOGO, pixels_to_text, where_it_runs, wordmark_bitmap


def test_local_backends_say_code_stays_local():
    for url in ("http://localhost:11434", "http://127.0.0.1:8080/v1"):
        assert "runs locally" in where_it_runs(url).plain


def test_remote_backends_name_the_host():
    line = where_it_runs("https://api.groq.com/openai/v1").plain
    assert "api.groq.com" in line
    assert "sent there" in line


def test_bitmaps_are_rectangular_and_render_two_rows_per_line():
    assert len({len(row) for row in LOGO}) == 1
    word = wordmark_bitmap("locoder")
    assert len({len(row) for row in word}) == 1
    # 14 logo rows -> 7 lines; 7 wordmark rows (padded to 8) -> 4 lines.
    assert len(pixels_to_text(LOGO, "").plain.splitlines()) == 7
    assert len(pixels_to_text(word, "").plain.splitlines()) == 4
