from __future__ import annotations

import csv
from pathlib import Path
import warnings

from .legacy.model import ModelTrainer
from .types import FeatureSnapshot


def export_features_for_qlib(features: FeatureSnapshot, output_dir: str | Path) -> dict[str, str]:
    """Export a stable CSV snapshot consumable by a Qlib data preparation job."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    feature_path = directory / "features.csv"
    names = sorted({name for row in features.rows for name in row.values})
    with feature_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["datetime", "instrument", *names])
        writer.writeheader()
        for row in features.rows:
            writer.writerow({
                "datetime": row.as_of_date.isoformat(),
                "instrument": row.ts_code,
                **{name: row.values.get(name) for name in names},
            })
    return {"features": str(feature_path), "factor_version": str(features.metadata.get("factor_version", "unknown"))}


def export_qlib(features: FeatureSnapshot, output_dir: str | Path) -> dict[str, str]:
    warnings.warn(
        "export_qlib() is deprecated; use export_features_for_qlib()",
        DeprecationWarning,
        stacklevel=2,
    )
    return export_features_for_qlib(features, output_dir)
