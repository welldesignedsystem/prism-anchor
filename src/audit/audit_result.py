from __future__ import annotations

from dataclasses import dataclass, field


# ── Audit result ───────────────────────────────────────────────────────────────

@dataclass
class AuditResult:
    handler:  str
    passed:   bool
    score:    float = 0.0           # 0.0 – 100.0
    findings: list[str] = field(default_factory=list)
    metadata: dict      = field(default_factory=dict)

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"<AuditResult {self.handler} {status} score={self.score:.1f}>"
