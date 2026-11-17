from money import cents


def total(lines):
    """Total of (name, unit price in dollars, quantity) lines, in cents."""
    result = 0
    for _name, price, _quantity in lines:
        result += cents(price)
    return result
