"""Exact USD minor units. Only requested principal has product bounds."""

import re
from typing import Annotated

from pydantic import Field, StrictInt

MIN_REQUEST_CENTS = 10_000
MAX_REQUEST_CENTS = 1_000_000
RequestedUsdCents = Annotated[StrictInt, Field(ge=MIN_REQUEST_CENTS, le=MAX_REQUEST_CENTS)]

_USD_TEXT = re.compile(r"([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.([0-9]{1,2}))?")


def parse_usd_cents(text: str) -> int:
    """Parse an unsigned dollar amount without rounding or applying lending limits."""
    match = _USD_TEXT.fullmatch(text.strip())
    if not match:
        raise ValueError("Expected a dollar amount with at most two decimal places")
    dollars, fraction = match.groups(default="")
    return int(dollars.replace(",", "")) * 100 + int(fraction.ljust(2, "0"))


def format_usd(cents: int) -> str:
    if type(cents) is not int:
        raise TypeError("USD amounts must be integer cents")
    dollars, fraction = divmod(abs(cents), 100)
    return f"{'-' if cents < 0 else ''}${dollars:,}.{fraction:02d}"
