"""Portfolio predicates shared by paged queries; point reads use the stored contract."""

from shared.contracts import USD_V1


def portfolio_sql(legacy: bool, *, krw: str | None = None, cents: str | None = None) -> str:
    if legacy:
        conditions = [f"NOT IS_DEFINED(c.{field})" for field in USD_V1.model_dump()]
        if krw:
            conditions += [f"IS_NUMBER(c.{krw})", f"NOT IS_DEFINED(c.{cents})"]
    else:
        conditions = [f"c.{k} = '{v}'" for k, v in USD_V1.model_dump().items()]
        if cents:
            conditions += [f"IS_NUMBER(c.{cents})", f"NOT IS_DEFINED(c.{krw})"]
    return "(" + " AND ".join(conditions) + ")"


def in_portfolio(doc: dict, legacy: bool) -> bool:
    product = USD_V1.model_dump()
    return (all(k not in doc for k in product) if legacy
            else all(doc.get(k) == v for k, v in product.items()))
