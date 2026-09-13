"""trace.py — Motor de Trazabilidad y Capa Determinista ("Chain of Work").

Diseñado para los retos enterprise de HackSpain (Maisa, HappyRobot, Prosper AI, Embat):
1. El LLM interpreta lenguaje natural/desestructurado.
2. El código determinista valida restricciones de negocio y genera un Trace auditable.
3. Las acciones sobre sistemas de registro (ERP, TMS, EHR) se formulan como
   propuestas revisables y reversibles (Silent / Guided / Escalate).

Uso:
    from trace import AuditTracer

    tracer = AuditTracer("reconciliation_worker")
    tracer.record_step("input_received", {"invoice_id": "INV-101", "amount": 1500.0})
    # ... operaciones de LLM y tools ...
    tracer.record_step("rule_validation", {"rule": "amount_within_limit", "passed": True})
    proposal = tracer.propose_action(
        action_type="bank_reconciliation",
        payload={"invoice_id": "INV-101", "bank_tx_id": "TX-994"},
        confidence=0.98,
        reversible=True
    )
    print(tracer.summary_table())
"""
from __future__ import annotations

import io
import json
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# Configurar encoding seguro en Windows para evitar charmap UnicodeEncodeError
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


@dataclass
class TraceStep:
    step_id: str
    name: str
    data: Dict[str, Any]
    status: str = "PASS"  # PASS, FAIL, WARNING
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0


@dataclass
class ActionProposal:
    proposal_id: str
    action_type: str
    payload: Dict[str, Any]
    confidence: float
    status: str = "PROPOSED"  # PROPOSED, EXECUTED, REJECTED, ROLLED_BACK
    requires_human_approval: bool = False
    reversible: bool = True
    created_at: float = field(default_factory=time.time)


class AuditTracer:
    """Registrador determinista de ejecuciones para auditoría y visualización."""

    def __init__(self, task_name: str, session_id: Optional[str] = None):
        self.task_name = task_name
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.start_time = time.time()
        self.steps: List[TraceStep] = []
        self.proposals: List[ActionProposal] = []
        self.metadata: Dict[str, Any] = {}

    def record_step(
        self,
        name: str,
        data: Dict[str, Any],
        status: str = "PASS",
        duration_ms: float = 0.0,
    ) -> TraceStep:
        """Registra un paso intermedio de razonamiento, tool o validación."""
        step = TraceStep(
            step_id=f"step_{len(self.steps) + 1}",
            name=name,
            data=data,
            status=status,
            duration_ms=duration_ms,
        )
        self.steps.append(step)
        return step

    def propose_action(
        self,
        action_type: str,
        payload: Dict[str, Any],
        confidence: float,
        confidence_threshold: float = 0.90,
        reversible: bool = True,
    ) -> ActionProposal:
        """Propone una mutación de estado con guardarraíl determinista.

        Si la confianza del modelo es inferior al umbral, fuerza revisión humana.
        """
        needs_human = confidence < confidence_threshold
        proposal = ActionProposal(
            proposal_id=f"prop_{len(self.proposals) + 1}_{str(uuid.uuid4())[:4]}",
            action_type=action_type,
            payload=payload,
            confidence=confidence,
            requires_human_approval=needs_human,
            status="PENDING_APPROVAL" if needs_human else "APPROVED",
            reversible=reversible,
        )
        self.proposals.append(proposal)
        self.record_step(
            f"action_proposal:{action_type}",
            {
                "proposal_id": proposal.proposal_id,
                "confidence": confidence,
                "threshold": confidence_threshold,
                "requires_approval": needs_human,
            },
            status="PASS" if not needs_human else "WARNING",
        )
        return proposal

    def execute_proposal(self, proposal_id: str) -> bool:
        """Simula la ejecución confirmada de una acción."""
        for p in self.proposals:
            if p.proposal_id == proposal_id:
                p.status = "EXECUTED"
                self.record_step(f"execute:{p.action_type}", {"proposal_id": proposal_id, "status": "EXECUTED"})
                return True
        return False

    def rollback_proposal(self, proposal_id: str) -> bool:
        """Simula la reversión / undo de una acción ejecutada."""
        for p in self.proposals:
            if p.proposal_id == proposal_id and p.reversible:
                p.status = "ROLLED_BACK"
                self.record_step(f"rollback:{p.action_type}", {"proposal_id": proposal_id, "status": "ROLLED_BACK"})
                return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Exporta la traza completa a diccionario JSON-serializable."""
        elapsed = time.time() - self.start_time
        return {
            "session_id": self.session_id,
            "task_name": self.task_name,
            "elapsed_seconds": round(elapsed, 3),
            "step_count": len(self.steps),
            "proposal_count": len(self.proposals),
            "steps": [asdict(s) for s in self.steps],
            "proposals": [asdict(p) for p in self.proposals],
            "metadata": self.metadata,
        }

    def summary_table(self) -> str:
        """Genera un resumen legible en texto para terminal."""
        lines = [
            f"=== CHAIN OF WORK AUDIT TRACE [{self.task_name} | {self.session_id}] ===",
            f"Duración: {time.time() - self.start_time:.2f}s | Pasos: {len(self.steps)} | Propuestas: {len(self.proposals)}",
            "-" * 65,
        ]
        for s in self.steps:
            icon = "OK" if s.status == "PASS" else ("WARN" if s.status == "WARNING" else "FAIL")
            lines.append(f"[{icon:<4}] {s.step_id:<8} {s.name:<25} {json.dumps(s.data, ensure_ascii=False)[:35]}")
        
        if self.proposals:
            lines.append("-" * 65)
            lines.append("ACCIONES PROPUESTAS / ESTADO DE ESCRITURA:")
            for p in self.proposals:
                flag = " [REQUIERE APROBACION]" if p.requires_human_approval else " [AUTONOMO]"
                lines.append(f"  * {p.proposal_id}: {p.action_type} (conf: {p.confidence:.1%}){flag} -> {p.status}")
        lines.append("=" * 65)
        return "\n".join(lines)


if __name__ == "__main__":
    t = AuditTracer("demo_invoice_reconciliation")
    t.record_step("ocr_parse", {"doc": "factura_sept2026.pdf", "fields_extracted": 8})
    t.record_step("deterministic_tax_check", {"tax_rate": 0.21, "valid": True})
    t.propose_action(
        action_type="erp_ledger_post",
        payload={"vendor": "Iberdrola", "subtotal": 1200.0, "tax": 252.0, "total": 1452.0},
        confidence=0.96,
        reversible=True,
    )
    t.propose_action(
        action_type="bank_transfer",
        payload={"beneficiary": "Iberdrola", "amount": 1452.0},
        confidence=0.82,  # Sub-90%: requerirá aprobación humana
        confidence_threshold=0.90,
        reversible=True,
    )
    print(t.summary_table())
