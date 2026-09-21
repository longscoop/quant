from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

import pandas as pd

from .markets import InstrumentMapper, MarketCalendar, ResearchContext, TimeSplitConfig


class QlibDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedQlibDataset:
    handler: Any
    dataset: Any
    frame: pd.DataFrame
    segments: dict[str, tuple]
    feature_columns: tuple[str, ...]
    audit: dict


class QlibDatasetAdapter:
    REQUIRED_COLUMNS = {
        "feature_date",
        "canonical_instrument_id",
        "market_id",
        "currency",
        "label",
        "label_end_date",
    }

    def __init__(self, context: ResearchContext, mapper: InstrumentMapper, feature_columns: Sequence[str]):
        if not feature_columns:
            raise ValueError("feature_columns must not be empty")
        self.context = context
        self.mapper = mapper
        self.feature_columns = tuple(dict.fromkeys(feature_columns))

    def to_qlib_frame(self, source: pd.DataFrame) -> pd.DataFrame:
        missing = self.REQUIRED_COLUMNS.union(self.feature_columns).difference(source.columns)
        if missing:
            raise ValueError(f"missing Qlib source columns: {', '.join(sorted(missing))}")
        markets = set(source["market_id"].dropna().astype(str).str.upper())
        if markets != {self.context.market_id}:
            raise ValueError(f"DatasetH must contain a single market {self.context.market_id}; got {sorted(markets)}")
        currencies = set(source["currency"].dropna().astype(str).str.upper())
        if currencies != {self.context.currency}:
            raise ValueError(f"dataset currency mismatch: expected {self.context.currency}, got {sorted(currencies)}")

        flat = source.copy()
        flat["datetime"] = pd.to_datetime(flat["feature_date"])
        flat["instrument"] = flat["canonical_instrument_id"].map(self.mapper.to_qlib)
        index_columns = ["datetime", "instrument"]
        if flat.duplicated(index_columns).any():
            raise ValueError("duplicate (datetime, instrument) index")
        flat = flat.sort_values(index_columns).set_index(index_columns)
        values: dict[tuple[str, str], pd.Series] = {
            ("feature", name): flat[name] for name in self.feature_columns
        }
        values[("label", "LABEL0")] = flat["label"]
        frame = pd.DataFrame(values, index=flat.index)
        frame.columns = pd.MultiIndex.from_tuples(frame.columns)
        frame.index = frame.index.set_names(["datetime", "instrument"])
        return frame

    def build_dataset(
        self,
        source: pd.DataFrame,
        split: TimeSplitConfig,
        calendar: MarketCalendar,
        *,
        handler_factory: Callable[[pd.DataFrame], Any] | None = None,
        dataset_factory: Callable[..., Any] | None = None,
    ) -> PreparedQlibDataset:
        segments, split_audit = split.effective_segments(calendar)
        prepared_source = source.copy()
        prepared_source["feature_date"] = pd.to_datetime(prepared_source["feature_date"]).dt.date
        prepared_source["label_end_date"] = pd.to_datetime(prepared_source["label_end_date"]).dt.date
        labeled_rows = prepared_source["label"].notna()
        for row in prepared_source.loc[labeled_rows, ["feature_date", "label_end_date"]].itertuples(index=False):
            try:
                expected_end = calendar.shift(row.feature_date, split.label_horizon)
            except ValueError as exc:
                raise ValueError(f"mature label is outside calendar coverage: {row.feature_date}") from exc
            if row.label_end_date != expected_end:
                raise ValueError(
                    f"label_end_date must equal calendar shift({row.feature_date}, {split.label_horizon})"
                )
        purge_masks = {
            "train": (
                prepared_source["feature_date"].between(split.train.start, split.train.end)
                & (prepared_source["label_end_date"] >= split.valid.start)
            ),
            "valid": (
                prepared_source["feature_date"].between(split.valid.start, split.valid.end)
                & (prepared_source["label_end_date"] >= split.test.start)
            ),
        }
        for name, mask in purge_masks.items():
            split_audit[name]["purged_rows"] = int(mask.sum())
        prepared_source = prepared_source.loc[~(purge_masks["train"] | purge_masks["valid"])]
        frame = self.to_qlib_frame(prepared_source)
        train_start, train_end = segments["train"]
        train = frame.loc[pd.IndexSlice[pd.Timestamp(train_start):pd.Timestamp(train_end), :], :]
        usable_features = tuple(
            name for name in self.feature_columns if not train[("feature", name)].isna().all()
        )
        if not usable_features:
            raise ValueError("train segment has no finite feature columns")
        keep_columns = [("feature", name) for name in usable_features] + [("label", "LABEL0")]
        frame = frame.loc[:, keep_columns]
        feature_block = frame.loc[:, "feature"]
        frame = frame.loc[~feature_block.isna().all(axis=1)]
        frame = frame.loc[frame[("label", "LABEL0")].notna()]

        if handler_factory is None or dataset_factory is None:
            try:
                from qlib.data.dataset import DatasetH
                from qlib.data.dataset.handler import DataHandlerLP
            except (ImportError, OSError) as exc:
                raise QlibDependencyError("pyqlib==0.9.7 is required for the MODEL workflow") from exc
            handler_factory = DataHandlerLP.from_df
            dataset_factory = DatasetH

        handler = handler_factory(frame)
        qlib_segments = {name: (start.isoformat(), end.isoformat()) for name, (start, end) in segments.items()}
        dataset = dataset_factory(handler=handler, segments=qlib_segments)
        return PreparedQlibDataset(
            handler=handler,
            dataset=dataset,
            frame=frame,
            segments=segments,
            feature_columns=usable_features,
            audit={"context": self.context.to_dict(), "split": split_audit},
        )

    def build_inference_dataset(
        self,
        source: pd.DataFrame,
        *,
        handler_factory: Callable[[pd.DataFrame], Any] | None = None,
        dataset_factory: Callable[..., Any] | None = None,
    ) -> PreparedQlibDataset:
        required = {
            "feature_date",
            "canonical_instrument_id",
            "market_id",
            "currency",
            *self.feature_columns,
        }
        missing = required.difference(source.columns)
        if missing:
            raise ValueError(f"missing Qlib inference columns: {', '.join(sorted(missing))}")
        augmented = source.copy()
        augmented["label"] = pd.NA
        augmented["label_end_date"] = pd.NaT
        frame = self.to_qlib_frame(augmented).drop(columns=[("label", "LABEL0")])
        frame = frame.loc[~frame.loc[:, "feature"].isna().all(axis=1)]
        if frame.empty:
            raise ValueError("inference contains no rows with usable features")
        if handler_factory is None or dataset_factory is None:
            try:
                from qlib.data.dataset import DatasetH
                from qlib.data.dataset.handler import DataHandlerLP
            except (ImportError, OSError) as exc:
                raise QlibDependencyError("pyqlib==0.9.7 is required for MODEL inference") from exc
            handler_factory = DataHandlerLP.from_df
            dataset_factory = DatasetH
        dates = frame.index.get_level_values("datetime")
        bounds = (dates.min().date(), dates.max().date())
        segments = {"inference": bounds}
        qlib_segments = {"inference": tuple(value.isoformat() for value in bounds)}
        handler = handler_factory(frame)
        dataset = dataset_factory(handler=handler, segments=qlib_segments)
        return PreparedQlibDataset(
            handler=handler,
            dataset=dataset,
            frame=frame,
            segments=segments,
            feature_columns=self.feature_columns,
            audit={"context": self.context.to_dict(), "mode": "inference", "label_required": False},
        )
