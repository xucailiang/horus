from collections.abc import Iterator
from typing import Any

from horus.adapters.hurl import MISSING, Response, compare, pointer
from horus.core.models import (
    Comparison,
    LiteralOperand,
    Operand,
    Predicate,
    PredicateGroup,
    PredicateNot,
    VariableOperand,
)
from horus.core.store import HorusError


def comparisons(predicate: Predicate) -> Iterator[Comparison]:
    if isinstance(predicate, Comparison):
        yield predicate
    elif isinstance(predicate, PredicateGroup):
        for child in predicate.predicates:
            yield from comparisons(child)
    else:
        yield from comparisons(predicate.predicate)


def guaranteed_variables(predicate: Predicate, truth: bool) -> set[str]:
    if isinstance(predicate, PredicateNot):
        return guaranteed_variables(predicate.predicate, not truth)
    if isinstance(predicate, PredicateGroup):
        if (predicate.kind == "all" and truth) or (predicate.kind == "any" and not truth):
            return set().union(*(guaranteed_variables(p, truth) for p in predicate.predicates))
    elif (
        predicate.operator == "exists"
        and isinstance(predicate.left, VariableOperand)
        and isinstance(predicate.right, LiteralOperand)
        and type(predicate.right.value) is bool
        and truth == predicate.right.value
    ):
        return {predicate.left.name}
    return set()


def operand_value(operand: Operand, variables: dict[str, Any], response: Response | None) -> Any:
    if isinstance(operand, LiteralOperand):
        return operand.value
    if isinstance(operand, VariableOperand):
        return variables.get(operand.name, MISSING)
    if response is None:
        raise HorusError("predicate_response_unavailable")
    if operand.jsonPointer is not None:
        return pointer(response.body, operand.jsonPointer)
    if operand.header is not None:
        return response.headers.get(operand.header.lower(), MISSING)
    return response.status


def evaluate(
    predicate: Predicate,
    variables: dict[str, Any],
    response: Response | None = None,
    observations: list[dict[str, Any]] | None = None,
) -> bool:
    if isinstance(predicate, PredicateNot):
        return not evaluate(predicate.predicate, variables, response, observations)
    if isinstance(predicate, PredicateGroup):
        # Evaluate every input so an absent or invalid field cannot hide behind short-circuiting.
        results = [
            evaluate(child, variables, response, observations) for child in predicate.predicates
        ]
        return all(results) if predicate.kind == "all" else any(results)
    left = operand_value(predicate.left, variables, response)
    right = operand_value(predicate.right, variables, response)
    if right is MISSING or (left is MISSING and predicate.operator != "exists"):
        raise HorusError("predicate_input_missing")
    valid = True
    if predicate.operator in {"gte", "lte"}:
        valid = type(left) in (int, float) and type(right) in (int, float)
    elif predicate.operator == "exists":
        valid = type(right) is bool
    elif predicate.operator == "length":
        valid = isinstance(left, (str, list, dict)) and type(right) is int and right >= 0
    elif predicate.operator == "type":
        valid = isinstance(right, str) and right in {
            "string", "integer", "number", "boolean", "array", "object", "null",
        }
    if not valid:
        raise HorusError("invalid_predicate_operand")
    matched = compare(left, predicate.operator, right)
    if observations is not None:
        observations.append({
            "left": None if left is MISSING else left,
            "leftMissing": left is MISSING,
            "operator": predicate.operator,
            "right": right,
            "matched": matched,
            "evidenceRefs": predicate.evidenceRefs,
        })
    return matched
