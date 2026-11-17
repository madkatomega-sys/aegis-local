def chunks(items, size):
    """Split items into consecutive lists of `size` items; the last list may be shorter."""
    result = []
    current = []
    for item in items:
        current.append(item)
        if len(current) > size:
            result.append(current)
            current = []
    return result
