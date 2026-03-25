"""Advanced analysis modules — military-grade QA beyond standard checks."""

from __future__ import annotations


def get_advanced_modules() -> list:
    """Return list of all advanced module instances."""
    from serenity.advanced.behavioral import BehavioralAnalyzer
    from serenity.advanced.cache_audit import CacheAuditor
    from serenity.advanced.chaos import ChaosEngineer
    from serenity.advanced.i18n import I18nTester
    from serenity.advanced.memory_leak import MemoryLeakDetector
    from serenity.advanced.network_analysis import NetworkAnalyzer
    from serenity.advanced.race_condition import RaceConditionDetector
    from serenity.advanced.websocket_sse import WebSocketAnalyzer

    return [
        BehavioralAnalyzer(),
        MemoryLeakDetector(),
        RaceConditionDetector(),
        NetworkAnalyzer(),
        WebSocketAnalyzer(),
        CacheAuditor(),
        ChaosEngineer(),
        I18nTester(),
    ]
