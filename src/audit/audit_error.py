from __future__ import annotations


# ── Exceptions ─────────────────────────────────────────────────────────────────

class AuditError(Exception):
    """Raised by an AuditHandler on a hard failure (not a short-circuit)."""
