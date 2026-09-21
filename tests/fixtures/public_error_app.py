"""Deterministic public exception boundary fixture for Phase 2 Task 5."""

from __future__ import annotations

import streamlit as st

from quant.ui import render_public_page


def _broken_public_renderer() -> None:
    raise RuntimeError(
        "TUSHARE_TOKEN=fixture-token postgresql://reader:password@db.example/research"
    )


render_public_page(st, _broken_public_renderer)
