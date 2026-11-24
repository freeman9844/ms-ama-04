"""Select the native record contract, never the UI locale or a model-provided unit."""
from collections.abc import Mapping
from typing import Any

from shared.contracts import UsdProduct


def product_of(record: Mapping[str, Any]) -> UsdProduct | None:
    fields = {k: record[k] for k in UsdProduct.model_fields if k in record}
    if not fields and not any(k.endswith("_cents") for k in record):
        return None
    if any(k.endswith("_krw") for k in record):
        raise ValueError("Mixed monetary contracts")
    return UsdProduct.model_validate(fields)


def validate_usd_evidence(value: Any) -> None:
    """Nested evidence inherits product; explicit tags and monetary fields cannot contradict it."""
    if isinstance(value, Mapping):
        if any(k in value for k in UsdProduct.model_fields):
            product_of(value)
        for k, v in value.items():
            if isinstance(k, str) and (k.endswith("_krw") or (k.endswith("_cents") and v is not None and type(v) is not int)):
                raise ValueError("Invalid monetary unit in USD evidence")
            validate_usd_evidence(v)
    elif isinstance(value, (tuple, list)):
        for item in value:
            validate_usd_evidence(item)
