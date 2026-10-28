"""Test scenarios for load testing.

Provides CPU-bound, memory-bound, I/O-bound, and multi-language scenarios.
"""

from .base import BaseScenario
from .cpu_bound import (
    CPULightScenario,
    CPUMediumScenario,
    CPUHeavyScenario,
    CPUSklearnScenario,
    QuotaHoldScenario,
)
from .memory_bound import (
    Memory10MBScenario,
    Memory50MBScenario,
    Memory100MBScenario,
    MemoryPandasScenario,
)
from .io_bound import (
    IOWriteSmallScenario,
    IOWriteLargeScenario,
    IOMatplotlibScenario,
    IOCSVScenario,
)
from .multi_language import (
    LanguageBaselineScenario,
    LanguageComputeScenario,
    get_all_language_scenarios,
)

# Registry of all scenarios by category
SCENARIOS = {
    "cpu": [
        CPULightScenario(),
        CPUMediumScenario(),
        CPUHeavyScenario(),
        CPUSklearnScenario(),
    ],
    "memory": [
        Memory10MBScenario(),
        Memory50MBScenario(),
        Memory100MBScenario(),
        MemoryPandasScenario(),
    ],
    "io": [
        IOWriteSmallScenario(),
        IOWriteLargeScenario(),
        IOMatplotlibScenario(),
        IOCSVScenario(),
    ],
    "language": get_all_language_scenarios(),
    "control": [QuotaHoldScenario()],
}


def get_all_scenarios() -> list:
    """Get all available scenarios."""
    all_scenarios = []
    for category_scenarios in SCENARIOS.values():
        all_scenarios.extend(category_scenarios)
    return all_scenarios


def get_scenarios_by_category(category: str) -> list:
    """Get scenarios by category."""
    return SCENARIOS.get(category, [])


def get_scenario_by_id(scenario_id: str):
    """Get a specific scenario by ID."""
    for scenario in get_all_scenarios():
        if scenario.id == scenario_id:
            return scenario
    return None


__all__ = [
    "BaseScenario",
    "SCENARIOS",
    "get_all_scenarios",
    "get_scenarios_by_category",
    "get_scenario_by_id",
]
