import pytest

from igdm.voice import problems

DENY = ["delve", "i'd be happy to", "certainly"]


@pytest.mark.parametrize("text", [
    "yes totally, what's your email and i'll get it to you",
    "done! made a printable version with a map. what's your email?",
    "it's at 10:30, what's your email and i'll send it over",
    "check https://example.com for the rest, what's your email?",
])
def test_clean(text):
    assert problems(text, DENY) == []


@pytest.mark.parametrize("text,why", [
    ("sure thing — what's your email?", "dash"),
    ("sure thing – what's your email?", "dash"),
    ("Quick update: it's ready, what's your email?", "label"),
    ("all good. got it. what's your email and i'll send it over.", None),
    ("All done. Sent over.", "fragment"),
    ("I'd be happy to send it, what's your email?", "deny"),
    ("let me delve into it, what's your email?", "deny"),
])
def test_problems(text, why):
    found = problems(text, DENY)
    if why is None:
        assert found == []
    else:
        assert found and any(why in p for p in found)


def test_deny_matches_whole_words_only():
    assert problems("that's certainlyish fine, what's your email and i'll send it?", DENY) == []
