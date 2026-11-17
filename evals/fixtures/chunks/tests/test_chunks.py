from chunks import chunks


def test_even_split():
    assert chunks([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_uneven_split_keeps_the_rest():
    assert chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
