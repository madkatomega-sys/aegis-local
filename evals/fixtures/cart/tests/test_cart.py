from cart import total


def test_total_counts_quantities():
    assert total([("pen", 1.50, 2), ("pad", 3.25, 1)]) == 625


def test_total_of_prices_that_do_not_convert_exactly():
    assert total([("tea", 19.99, 3)]) == 5997
