"""ABOUTME: Bounded decision gates with configurable local and hosted backends.
ABOUTME: Exposes the config-independent decision cascade used by the executor.
"""

from proceda.decision.service import ChoiceResult, DecisionService, GateResult

__all__ = ["ChoiceResult", "DecisionService", "GateResult"]
