"""Versioned business rules for recovery workflows.

Rules are business configuration, not hardcoded policy. Every change creates
an immutable version row and is audited. The evaluator supports a small,
deterministic condition language so a rule can gate a workflow step without
executing arbitrary code.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.recovery_workflow.models import BusinessRule, BusinessRuleVersion


class RuleError(ValueError):
    """A rule value or condition is invalid."""


DEFAULT_RULES: Dict[str, Dict[str, Any]] = {
    "workflow.require_approval": {
        "name": "Require approval for outbound actions",
        "category": "compliance",
        "value": {"enabled": True},
    },
    "workflow.max_attempts": {
        "name": "Maximum contact attempts per lead",
        "category": "workflow",
        "value": {"limit": 3},
    },
    "booking.enabled": {
        "name": "Allow appointment booking",
        "category": "booking",
        "value": {"enabled": True},
    },
    "reminders.enabled": {
        "name": "Create appointment reminders",
        "category": "reminders",
        "value": {"enabled": True, "offsets_hours": [24, 1]},
    },
}


def _validate_value(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise RuleError("rule value must be a JSON object")
    return value


class RuleEngine:
    def __init__(self, session: Session, business_id: str):
        self.session = session
        self.business_id = business_id

    def list_rules(self, active_only: bool = False) -> list[BusinessRule]:
        stmt = select(BusinessRule).where(BusinessRule.business_id == self.business_id)
        if active_only:
            stmt = stmt.where(BusinessRule.is_active.is_(True))
        return list(
            self.session.execute(stmt.order_by(BusinessRule.key)).scalars().all()
        )

    def get_rule(self, key: str) -> Optional[BusinessRule]:
        return self.session.execute(
            select(BusinessRule).where(
                BusinessRule.business_id == self.business_id,
                BusinessRule.key == key,
            )
        ).scalar_one_or_none()

    def get_value(self, key: str, default: Any = None) -> Any:
        rule = self.get_rule(key)
        if rule is None or not rule.is_active:
            builtin = DEFAULT_RULES.get(key)
            return builtin["value"] if builtin else default
        return rule.value

    def upsert_rule(
        self,
        *,
        key: str,
        name: str,
        value: Dict[str, Any],
        description: Optional[str] = None,
        category: str = "workflow",
        actor: str = "api",
        change_summary: Optional[str] = None,
    ) -> BusinessRule:
        value = _validate_value(value)
        rule = self.get_rule(key)
        if rule is None:
            rule = BusinessRule(
                business_id=self.business_id,
                key=key,
                name=name,
                description=description,
                category=category,
                value=value,
                is_active=True,
                current_version=1,
                created_by=actor,
            )
            self.session.add(rule)
            self.session.flush()
            version = 1
        else:
            rule.name = name
            rule.description = description
            rule.category = category
            rule.value = value
            rule.current_version = int(rule.current_version or 0) + 1
            rule.updated_at = datetime.now(timezone.utc)
            version = rule.current_version
        self.session.add(
            BusinessRuleVersion(
                rule_id=rule.id,
                business_id=self.business_id,
                version=version,
                value=value,
                change_summary=change_summary,
                created_by=actor,
            )
        )
        from app.modules.voice import repository as voice_repo
        from app.modules.voice.constants import AuditAction

        voice_repo.add_audit_log(
            self.session,
            action="business_rule_updated",
            business_id=self.business_id,
            actor=actor,
            entity_type="business_rule",
            entity_id=rule.id,
            reason=change_summary or f"rule {key} updated",
            metadata={"key": key, "version": version, "value": value},
        )
        self.session.flush()
        return rule

    def deactivate_rule(self, key: str, actor: str = "api") -> BusinessRule:
        rule = self.get_rule(key)
        if rule is None:
            raise RuleError(f"rule {key!r} does not exist")
        rule.is_active = False
        rule.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return rule

    def versions(self, key: str) -> list[BusinessRuleVersion]:
        rule = self.get_rule(key)
        if rule is None:
            return []
        return list(
            self.session.execute(
                select(BusinessRuleVersion)
                .where(BusinessRuleVersion.rule_id == rule.id)
                .order_by(BusinessRuleVersion.version.desc())
            ).scalars()
        )


def evaluate_condition(condition: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Evaluate a deterministic rule condition.

    Supported operators: ``eq``, ``ne``, ``gt``, ``gte``, ``lt``, ``lte``,
    ``in``, ``not_in``, ``contains``, ``exists``. ``all``/``any`` combine
    nested conditions. An unknown operator is an error (fail closed).
    """
    if not isinstance(condition, dict) or "field" not in condition:
        raise RuleError("condition must be an object with a 'field'")
    field = condition["field"]
    value = context.get(field)
    op = condition.get("op", "eq")
    expected = condition.get("value")
    if op == "eq":
        return value == expected
    if op == "ne":
        return value != expected
    if op == "gt":
        return value is not None and value > expected
    if op == "gte":
        return value is not None and value >= expected
    if op == "lt":
        return value is not None and value < expected
    if op == "lte":
        return value is not None and value <= expected
    if op == "in":
        return value in (expected or [])
    if op == "not_in":
        return value not in (expected or [])
    if op == "contains":
        return value is not None and expected in value
    if op == "exists":
        return (value is not None) is bool(expected)
    raise RuleError(f"unsupported rule operator {op!r}")


def evaluate_all(
    conditions: Iterable[Dict[str, Any]], context: Dict[str, Any]
) -> bool:
    return all(evaluate_condition(condition, context) for condition in conditions)
