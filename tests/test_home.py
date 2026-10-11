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


def test_snake_animation_is_three_squares_on_an_eight_frame_loop():
    from locoder.display import _QUADRANTS, SNAKE_FRAMES, snake_frame

    frames = [snake_frame(i) for i in range(SNAKE_FRAMES)]
    assert len(set(frames)) == SNAKE_FRAMES
    assert snake_frame(SNAKE_FRAMES) == frames[0]
    for frame in frames:
        assert len(frame) == 2
        squares = sum(bin(_QUADRANTS.index(ch)).count("1") for ch in frame)
        assert squares == 3
