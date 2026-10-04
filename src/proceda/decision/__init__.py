"""ABOUTME: Bounded decision gates with configurable local and hosted backends.
ABOUTME: Exposes the config-independent decision cascade used by the executor.
"""

from proceda.decision.service import DecisionService, GateResult

__all__ = ["DecisionService", "GateResult"]
