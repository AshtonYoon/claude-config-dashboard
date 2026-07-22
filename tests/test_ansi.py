"""ANSI color/gradient helpers: disabled path must be a pure passthrough,
enabled path must emit well-formed 24-bit escape codes."""

import io

from claude_config_dashboard import ansi


class TestSupportsColor:
    def test_force_true_wins(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert ansi.supports_color(force=True) is True

    def test_force_false_wins_even_on_a_tty(self, monkeypatch):
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert ansi.supports_color(force=False) is False

    def test_no_color_env_disables(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert ansi.supports_color() is False

    def test_force_color_env_enables(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert ansi.supports_color() is True

    def test_defaults_to_tty_detection(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        non_tty = io.StringIO()
        assert ansi.supports_color(stream=non_tty) is False


class TestStyleAndColor:
    def test_disabled_is_pure_passthrough(self):
        assert ansi.style("hi", ansi.BOLD, enabled=False) == "hi"
        assert ansi.color("hi", ansi.BRAND, enabled=False) == "hi"

    def test_empty_text_is_untouched_even_when_enabled(self):
        assert ansi.style("", ansi.BOLD, enabled=True) == ""

    def test_enabled_wraps_without_splitting_the_text(self):
        out = ansi.color("46,708", ansi.BRAND, enabled=True)
        # the literal substring must survive intact -- --report's tests grep for it
        assert "46,708" in out
        assert out.startswith("\x1b[")
        assert out.endswith(ansi.RESET)

    def test_bold_adds_bold_code(self):
        out = ansi.color("x", ansi.BRAND, bold=True, enabled=True)
        assert ansi.BOLD in out


class TestGradientText:
    def test_disabled_is_passthrough(self):
        assert ansi.gradient_text("abc", (0, 0, 0), (255, 255, 255), enabled=False) == "abc"

    def test_endpoints_match_start_and_end_colors(self):
        out = ansi.gradient_text("ab", (10, 20, 30), (200, 210, 220), enabled=True)
        assert "\x1b[38;2;10;20;30m" in out
        assert "\x1b[38;2;200;210;220m" in out

    def test_single_char_uses_start_color(self):
        out = ansi.gradient_text("a", (5, 5, 5), (250, 250, 250), enabled=True)
        assert "\x1b[38;2;5;5;5m" in out


class TestGradientBar:
    def test_disabled_uses_ascii_fallback(self):
        bar = ansi.gradient_bar(0.5, 10, ansi.BRAND, ansi.BRAND_DEEP, enabled=False)
        assert bar == "#####-----"

    def test_enabled_fill_matches_fraction(self):
        bar = ansi.gradient_bar(0.3, 10, ansi.BRAND, ansi.BRAND_DEEP, enabled=True)
        assert bar.count("█") == 3
        assert bar.count("░") == 7

    def test_clamps_out_of_range_fractions(self):
        assert ansi.gradient_bar(-1, 5, ansi.BRAND, ansi.BRAND_DEEP, enabled=False) == "-----"
        assert ansi.gradient_bar(2, 5, ansi.BRAND, ansi.BRAND_DEEP, enabled=False) == "#####"


class TestVerdictColor:
    def test_low_is_good(self):
        assert ansi.verdict_color(5) == ansi.GOOD

    def test_mid_is_warn(self):
        assert ansi.verdict_color(35) == ansi.WARN

    def test_high_is_bad(self):
        assert ansi.verdict_color(80) == ansi.BAD

    def test_boundaries(self):
        assert ansi.verdict_color(20) == ansi.WARN
        assert ansi.verdict_color(50) == ansi.BAD
