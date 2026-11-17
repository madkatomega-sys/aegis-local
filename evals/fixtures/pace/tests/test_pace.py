import pytest
from pace import pace


def test_ten_km_in_fifty_minutes():
    assert pace(10, 50) == pytest.approx(5.0)


def test_half_marathon():
    assert pace(21.0975, 105.4875) == pytest.approx(5.0)
