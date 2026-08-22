"""Sogi patch engine: structural edit proposals under governance.

- :mod:`.assessment`  deterministic post-hoc diff analysis (tampering, scope, risk)
- :mod:`.provider`    the ``PatchProvider`` port and the hash-guarded region editor
- :mod:`.ast_grep`    ast-grep-backed pattern rewrites
"""

from .assessment import PatchAssessment, analyze_patch
from .ast_grep import AstGrepPatchProvider
from .provider import (
    AppliedPatch,
    PatchError,
    PatchProposal,
    PatchProvider,
    PatchTarget,
    PatchToolUnavailable,
    RegionPatchProvider,
    StaleTargetError,
)

__all__ = [
    "AppliedPatch",
    "AstGrepPatchProvider",
    "PatchAssessment",
    "PatchError",
    "PatchProposal",
    "PatchProvider",
    "PatchTarget",
    "PatchToolUnavailable",
    "RegionPatchProvider",
    "StaleTargetError",
    "analyze_patch",
]
