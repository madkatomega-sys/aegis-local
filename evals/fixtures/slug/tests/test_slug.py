from slug import slugify


def test_lowercases_and_joins_words_with_hyphens():
    assert slugify("Hello, World!") == "hello-world"


def test_collapses_spaces_and_trims():
    assert slugify("  Many   spaces here ") == "many-spaces-here"


def test_keeps_digits_and_existing_hyphens():
    assert slugify("Release 2-beta") == "release-2-beta"
