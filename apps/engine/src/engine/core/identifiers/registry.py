"""
Identifier Registry - the pluggable set of active identifiers.

This is deliberately the smallest possible thing: a list plus two
indexes (by listened event type, and by run_mode). Adding a new
identifier to the engine is "instantiate it and add it to
`default_registry()`" - nothing else in the engine needs to change,
which is the whole point of the "pluggable" in the architecture
diagram.
"""
from __future__ import annotations

from collections import defaultdict

from engine.core.event_bus import WILDCARD
from engine.core.identifiers.base import Identifier, IdentifierRunMode


class IdentifierRegistry:
    def __init__(self, identifiers: list[Identifier] | None = None) -> None:
        self.identifiers: list[Identifier] = identifiers or []
        self._by_event_type: dict[str, list[Identifier]] = defaultdict(list)
        for identifier in self.identifiers:
            for event_type in identifier.listens_to:
                self._by_event_type[event_type].append(identifier)

    def register(self, identifier: Identifier) -> None:
        self.identifiers.append(identifier)
        for event_type in identifier.listens_to:
            self._by_event_type[event_type].append(identifier)

    def continuous_for_event_type(self, event_type: str) -> list[Identifier]:
        candidates = self._by_event_type.get(event_type, []) + self._by_event_type.get(WILDCARD, [])
        return [
            i
            for i in candidates
            if i.run_mode in (IdentifierRunMode.CONTINUOUS, IdentifierRunMode.BOTH)
        ]

    def one_time(self) -> list[Identifier]:
        return [
            i
            for i in self.identifiers
            if i.run_mode in (IdentifierRunMode.ONE_TIME, IdentifierRunMode.BOTH)
        ]


def default_registry() -> IdentifierRegistry:
    """The identifier set the engine ships with today. Import is local
    to avoid a hard dependency cycle (identifiers import from
    engine.core, not the other way around) and to make it obvious this
    is the one function to edit when adding/removing identifiers."""
    from engine.identifiers.name_match import NameMatchIdentifier
    from engine.identifiers.qa_pattern import QuestionAnsweringPatternIdentifier
    from engine.identifiers.screenshare_heuristic import ScreenshareHeuristicIdentifier
    from engine.identifiers.speaking_share import SpeakingShareIdentifier

    return IdentifierRegistry(
        [
            NameMatchIdentifier(),
            SpeakingShareIdentifier(),
            QuestionAnsweringPatternIdentifier(),
            ScreenshareHeuristicIdentifier(),
        ]
    )
