from money import cents


def test_cents_rounds_to_the_nearest_cent():
    assert cents(19.99) == 1999
    assert cents(0.29) == 29
