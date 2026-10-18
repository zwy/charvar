from __future__ import annotations

from random import Random

from charvar.wildcard import expand_inline, parse_options, rng_for, seed_for


def test_seed_for_is_stable_across_runs():
    assert seed_for(42, "full_front") == seed_for(42, "full_front")
    assert seed_for(42, "full_front") != seed_for(42, "full_l45")
    assert seed_for(42, "full_front") != seed_for(43, "full_front")


def test_expand_inline_trims_and_randomizes():
    rng = Random(7)
    text, count = expand_inline("{  gray | red } bg", rng)
    assert count == 1
    assert text in ("gray bg", "red bg")


def test_expand_inline_multiple_wildcards():
    text, count = expand_inline("[{a|b}] {x|y}", Random(1))
    assert count == 2
    assert text in ("[a] x", "[a] y", "[b] x", "[b] y")


def test_placeholder_is_left_alone():
    rng = Random(0)
    text, count = expand_inline("shot of one {subject_noun} facing", rng)
    assert count == 0
    assert text == "shot of one {subject_noun} facing"


def test_rng_for_is_deterministic():
    first = [rng_for(99, "k").choice(["x", "y", "z"]) for _ in range(5)]
    again = [rng_for(99, "k").choice(["x", "y", "z"]) for _ in range(5)]
    assert first == again


def test_parse_options():
    assert parse_options(" gray | gradient blue white | red ") == ["gray", "gradient blue white", "red"]
