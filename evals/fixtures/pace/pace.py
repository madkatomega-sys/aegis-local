from pint import UnitRegistry

ureg = UnitRegistry()


def pace(distance_km, minutes):
    """Running pace in minutes per kilometre."""
    speed = (distance_km * ureg.kilometer) / (minutes * ureg.minute)
    return (1 / speed).to(ureg.minute / ureg.mile).magnitude
