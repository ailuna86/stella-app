#!/usr/bin/env python3
"""V3.3 profiler wrapper.

V3.3 does not change profiling logic. It reuses V3.2-HIGH / V3.2 profile format
and lets the V3.3 scorer adapter apply the constrained post-score guards.
"""
from __future__ import annotations

try:
    from premium_metric_profiler_v3_2_high import *  # type: ignore
except Exception:  # pragma: no cover
    from premium_metric_profiler_v3_2 import *  # type: ignore

PROFILE_VERSION = "premium_metric_profile_v3_3_wrapper_uses_v3_2_high_profile"
