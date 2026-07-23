"""Shared pytest configuration.

Requirement refs: n/a (test infrastructure)
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings

# Hypothesis's data-generation health check measures wall-clock time, so it
# reports failures when the machine is busy rather than when a strategy is
# genuinely pathological. That makes it a source of flaky failures on a
# developer machine or a shared CI runner, and a test that fails randomly
# teaches people to ignore failures.
#
# Only the timing check is suppressed. Every check that can detect an actual
# defect — filtering too much, a non-reproducible strategy, unsatisfiable
# assumptions — stays enabled.
settings.register_profile(
    "default",
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.load_profile("default")
