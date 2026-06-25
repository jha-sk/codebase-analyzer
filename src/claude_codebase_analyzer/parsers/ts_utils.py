"""Shared tree-sitter helpers.

This module isolates all direct interaction with the ``tree_sitter`` API so the
individual language parsers can stay focused on language semantics. Grammars are
loaded lazily and cached, because importing every grammar at module load time is
both slow and unnecessary when only one language is being analyzed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Any

logger = logging.getLogger(__name__)

# Cache of language name -> tree_sitter.Language. Populated on demand.
_LANGUAGE_CACHE: dict[str, Any] = {}


def _load_language(name: str) -> Any:
    """Load and cache a tree-sitter ``Language`` by canonical name.

    Args:
        name: One of ``"go"``, ``"java"``, ``"python"``, ``"typescript"`` or
            ``"tsx"``.

    Returns:
        A ``tree_sitter.Language`` instance.

    Raises:
        ValueError: If ``name`` is not a supported grammar.
        ImportError: If the corresponding grammar package is not installed.
    """
    if name in _LANGUAGE_CACHE:
        return _LANGUAGE_CACHE[name]

    import tree_sitter

    if name == "go":
        import tree_sitter_go

        lang = tree_sitter.Language(tree_sitter_go.language())
    elif name == "java":
        import tree_sitter_java

        lang = tree_sitter.Language(tree_sitter_java.language())
    elif name == "python":
        import tree_sitter_python

        lang = tree_sitter.Language(tree_sitter_python.language())
    elif name == "typescript":
        import tree_sitter_typescript

        lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())
    elif name == "tsx":
        import tree_sitter_typescript

        lang = tree_sitter.Language(tree_sitter_typescript.language_tsx())
    else:
        raise ValueError(f"Unsupported tree-sitter grammar: {name!r}")

    _LANGUAGE_CACHE[name] = lang
    return lang


def get_parser(name: str) -> Any:
    """Return a fresh ``tree_sitter.Parser`` configured for ``name``.

    Args:
        name: Canonical grammar name (see :func:`_load_language`).

    Returns:
        A configured ``tree_sitter.Parser``.
    """
    import tree_sitter

    return tree_sitter.Parser(_load_language(name))


def parse_source(name: str, content: str) -> Any:
    """Parse ``content`` with the grammar ``name`` and return the root node.

    Args:
        name: Canonical grammar name.
        content: Source code to parse.

    Returns:
        The root ``tree_sitter.Node`` of the parsed tree.
    """
    parser = get_parser(name)
    tree = parser.parse(content.encode("utf-8"))
    return tree.root_node


def node_text(node: Any) -> str:
    """Return the UTF-8 decoded text covered by ``node``.

    Args:
        node: A ``tree_sitter.Node``.

    Returns:
        The decoded source text for the node (empty string if unavailable).
    """
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def walk(node: Any) -> Iterator[Any]:
    """Yield ``node`` and all of its descendants in pre-order.

    Args:
        node: The root ``tree_sitter.Node`` to traverse.

    Yields:
        Every node in the subtree, starting with ``node`` itself.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        # Reverse so children are visited left-to-right.
        stack.extend(reversed(current.children))


def find_all(node: Any, *types: str) -> list[Any]:
    """Collect all descendant nodes whose ``type`` is in ``types``.

    Args:
        node: Root node to search under.
        *types: One or more node type names to match.

    Returns:
        A list of matching nodes in pre-order.
    """
    wanted = set(types)
    return [n for n in walk(node) if n.type in wanted]


def first_child_of_type(node: Any, type_name: str) -> Any | None:
    """Return the first direct child of ``node`` with the given type.

    Args:
        node: Parent node.
        type_name: Child node type to look for.

    Returns:
        The matching child node, or ``None``.
    """
    for child in node.children:
        if child.type == type_name:
            return child
    return None


# Node types that represent a branch/decision point, keyed by grammar name.
# Used for a language-agnostic cyclomatic-complexity estimate.
_BRANCH_NODE_TYPES: dict[str, set[str]] = {
    "go": {
        "if_statement",
        "for_statement",
        "expression_switch_statement",
        "type_switch_statement",
        "select_statement",
        "case_clause",
        "communication_case",
    },
    "java": {
        "if_statement",
        "for_statement",
        "enhanced_for_statement",
        "while_statement",
        "do_statement",
        "switch_label",
        "catch_clause",
        "ternary_expression",
    },
    "python": {
        "if_statement",
        "elif_clause",
        "for_statement",
        "while_statement",
        "except_clause",
        "with_statement",
        "conditional_expression",
        "boolean_operator",
    },
    "typescript": {
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "switch_case",
        "catch_clause",
        "ternary_expression",
    },
}

# Binary boolean operators counted as additional branch points (where the grammar
# represents them as a generic binary_expression with an operator child).
_BOOLEAN_OPERATORS = {"&&", "||", "and", "or"}


def count_branch_points(node: Any, grammar: str) -> int:
    """Estimate cyclomatic complexity by counting branch points.

    The estimate is the number of decision nodes plus boolean short-circuit
    operators plus one (the base path). This is a deliberately simple,
    language-agnostic heuristic — not a substitute for a full control-flow
    analysis, but adequate for relative risk scoring.

    Args:
        node: Root node (typically a whole file or a function body).
        grammar: Canonical grammar name used to select the branch node set.

    Returns:
        An integer complexity estimate (>= 1).
    """
    branch_types = _BRANCH_NODE_TYPES.get(grammar, set())
    complexity = 1
    for current in walk(node):
        if current.type in branch_types:
            complexity += 1
        elif current.type in ("binary_expression", "boolean_operator"):
            # Count short-circuit boolean operators as decision points.
            op_text = _operator_text(current)
            if op_text in _BOOLEAN_OPERATORS:
                complexity += 1
    return complexity


def _operator_text(node: Any) -> str | None:
    """Best-effort extraction of a binary expression's operator token.

    Args:
        node: A binary expression node.

    Returns:
        The operator token text, or ``None`` if it cannot be determined.
    """
    op = node.child_by_field_name("operator")
    if op is not None:
        return node_text(op)
    # Fall back to scanning anonymous (non-named) children for an operator token.
    for child in node.children:
        if not child.is_named:
            text = node_text(child)
            if text in _BOOLEAN_OPERATORS:
                return text
    return None


def for_each_named(node: Any, callback: Callable[[Any], None]) -> None:
    """Invoke ``callback`` for every named descendant of ``node``.

    Args:
        node: Root node.
        callback: Function called with each named node.
    """
    for current in walk(node):
        if current.is_named:
            callback(current)


# ---------------------------------------------------------------------------
# Function / call-site extraction (used to build the function-level call graph)
# ---------------------------------------------------------------------------

# Node types that declare a callable, keyed by grammar.
_FUNCTION_DEF_TYPES: dict[str, set[str]] = {
    "go": {"function_declaration", "method_declaration"},
    "java": {"method_declaration", "constructor_declaration"},
    "python": {"function_definition"},
    "typescript": {
        "function_declaration",
        "method_definition",
        "function_expression",
        "arrow_function",
        "generator_function_declaration",
    },
}

# Node types representing a call/invocation, keyed by grammar.
_CALL_TYPES: dict[str, set[str]] = {
    "go": {"call_expression"},
    "java": {"method_invocation"},
    "python": {"call"},
    "typescript": {"call_expression"},
}

# Sentinel caller name for calls made at module top level (outside any function).
MODULE_SCOPE = "<module>"


def _def_name(node: Any, grammar: str) -> str | None:
    """Extract the declared name of a function/method definition node.

    For TypeScript arrow/function expressions (which are anonymous), the name is
    inferred from an enclosing ``variable_declarator`` or ``pair`` when present.

    Args:
        node: A function-definition node.
        grammar: Canonical grammar name.

    Returns:
        The function name, or ``None`` if it cannot be determined.
    """
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return node_text(name_node)

    if grammar == "typescript" and node.type in (
        "arrow_function",
        "function_expression",
    ):
        # `const foo = () => {}` / `foo: () => {}` -> use the binding name.
        parent = node.parent
        if parent is not None and parent.type in ("variable_declarator", "pair"):
            key = parent.child_by_field_name("name") or parent.child_by_field_name("key")
            if key is not None:
                return node_text(key)
    return None


def _callee_name(node: Any, grammar: str) -> str | None:
    """Extract the simple (unqualified) callee name from a call node.

    For member/selector/attribute calls (``a.b.c()``) the trailing identifier is
    returned (``c``).

    Args:
        node: A call/invocation node.
        grammar: Canonical grammar name.

    Returns:
        The callee's simple name, or ``None``.
    """
    if grammar == "java":
        name_node = node.child_by_field_name("name")
        return node_text(name_node) if name_node is not None else None

    callee = node.child_by_field_name("function")
    if callee is None and node.children:
        callee = node.children[0]
    if callee is None:
        return None

    if callee.type == "identifier":
        return node_text(callee)

    # member_expression / selector_expression / attribute -> trailing identifier.
    for field in ("property", "field", "attribute"):
        sub = callee.child_by_field_name(field)
        if sub is not None:
            return node_text(sub)

    # Fall back to the last identifier-like token within the callee expression.
    last: str | None = None
    for descendant in walk(callee):
        if descendant.type in ("identifier", "property_identifier", "field_identifier"):
            last = node_text(descendant)
    return last


def extract_symbols(root: Any, grammar: str) -> dict[str, list[dict]]:
    """Extract function definitions and call sites from a parsed tree.

    Each call is attributed to the innermost enclosing function (or
    :data:`MODULE_SCOPE` for top-level calls), enabling a function-to-function
    call graph to be built.

    Args:
        root: The root tree-sitter node for a file.
        grammar: Canonical grammar name.

    Returns:
        A dict with two keys:

        ``functions``: list of ``{"name", "start_line", "end_line"}``.
        ``calls``: list of ``{"caller", "callee", "line"}``.
    """
    func_types = _FUNCTION_DEF_TYPES.get(grammar, set())
    call_types = _CALL_TYPES.get(grammar, set())
    functions: list[dict] = []
    calls: list[dict] = []

    def visit(node: Any, caller: str) -> None:
        current_caller = caller
        if node.type in func_types:
            name = _def_name(node, grammar)
            if name:
                functions.append(
                    {
                        "name": name,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                    }
                )
                current_caller = name
        elif node.type in call_types:
            callee = _callee_name(node, grammar)
            if callee:
                calls.append(
                    {
                        "caller": caller,
                        "callee": callee,
                        "line": node.start_point[0] + 1,
                    }
                )
        for child in node.children:
            visit(child, current_caller)

    visit(root, MODULE_SCOPE)
    return {"functions": functions, "calls": calls}
