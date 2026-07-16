"""
ProcessorRegistry - the pluggable, dependency-ordered set of active
Processors (a superset of the old flat IdentifierRegistry - an
Identifier *is* a Processor, see core/identifiers/base.py). Adding a
new Processor or Identifier to the engine is still "instantiate it and
add it to `default_registry()`" - the registry is what turns a flat
list plus each entry's own `depends_on` into:

  1. A validated DAG (build-time failure, not a silent runtime
     surprise, if two processors' `depends_on` form a cycle, or if one
     declares a dependency on an id that isn't registered at all).
  2. A "layer" per processor (0 = no dependencies, N = one more than
     the deepest of its own dependencies) - this is what lets
     `continuous_for_event_type`/`one_time` return their processors in
     an order that's always safe to run top-to-bottom within a single
     event's dispatch: nothing ever gets invoked before something it
     depends on, *when they're triggered by the same event*. (If two
     dependent processors listen to different event types, ordering
     between them is moot - see `enabled` below and
     core/feature_cache.py for how the actually-relevant "is my
     dependency's output there yet" question is answered instead.)
  3. `enabled`, computed once and stamped onto each processor instance
     - True iff at least one Identifier is reachable from it via the
     dependency graph, i.e. something eventually consumes its output.
     Reachability, not "does anyone list me directly" - a processor
     three hops upstream of the nearest Identifier is just as enabled
     as one hop upstream, otherwise a mid-chain processor being added
     later would silently go dead the moment something above it in
     the chain is what actually gets depended on.
"""
from __future__ import annotations

from collections import defaultdict, deque

from engine.core.event_bus import WILDCARD
from engine.core.identifiers.base import Identifier
from engine.core.processor import Processor, ProcessorRunMode


class DependencyCycleError(ValueError):
    """Raised at registry-build time when two or more processors'
    `depends_on` declarations form a cycle. This can only be a wiring
    bug - nothing in this architecture has a legitimate circular
    dependency - so the engine fails loudly at startup instead of
    silently running processors in an undefined order."""


class ProcessorRegistry:
    def __init__(self, processors: list[Processor] | None = None) -> None:
        self.processors: list[Processor] = list(processors or [])

        self._by_id: dict[str, Processor] = {}
        for processor in self.processors:
            if processor.id in self._by_id:
                raise ValueError(f"duplicate processor id {processor.id!r}")
            self._by_id[processor.id] = processor

        self._validate_dependencies_exist()
        self._layer_index: dict[str, int] = self._compute_layers()
        self._compute_enabled()

        self._by_event_type: dict[str, list[Processor]] = defaultdict(list)
        for processor in self.processors:
            for event_type in processor.listens_to:
                self._by_event_type[event_type].append(processor)

    # -- back-compat / convenience ----------------------------------------

    @property
    def identifiers(self) -> list[Identifier]:
        """The emitting subset - existing call sites that only ever
        cared about Identifiers (e.g. looking up `.weight` by id in
        SessionEngine._consume_evidence) keep working unchanged."""
        return [p for p in self.processors if isinstance(p, Identifier)]

    def get(self, processor_id: str) -> Processor | None:
        return self._by_id.get(processor_id)

    def layer_of(self, processor_id: str) -> int:
        return self._layer_index[processor_id]

    # -- dispatch-facing queries --------------------------------------------

    def continuous_for_event_type(self, event_type: str) -> list[Processor]:
        """Every CONTINUOUS/BOTH processor subscribed to `event_type`
        (plus WILDCARD subscribers), in dependency order - a processor
        never appears before something it (directly or transitively)
        depends on that's also in this list."""
        candidates = self._by_event_type.get(event_type, []) + self._by_event_type.get(WILDCARD, [])
        seen: set[str] = set()
        deduped: list[Processor] = []
        for processor in candidates:
            if processor.id in seen:
                continue
            seen.add(processor.id)
            deduped.append(processor)
        deduped.sort(key=lambda p: self._layer_index[p.id])
        return [p for p in deduped if p.run_mode in (ProcessorRunMode.CONTINUOUS, ProcessorRunMode.BOTH)]

    def one_time(self) -> list[Processor]:
        """Every ONE_TIME/BOTH processor, in dependency order - same
        reasoning as `continuous_for_event_type`, applied to the
        "Initial One Time Run" step instead of the event bus."""
        candidates = [p for p in self.processors if p.run_mode in (ProcessorRunMode.ONE_TIME, ProcessorRunMode.BOTH)]
        return sorted(candidates, key=lambda p: self._layer_index[p.id])

    # -- build-time graph analysis ------------------------------------------

    def _validate_dependencies_exist(self) -> None:
        for processor in self.processors:
            for dep_id in processor.depends_on:
                if dep_id not in self._by_id:
                    raise ValueError(
                        f"processor {processor.id!r} declares depends_on={dep_id!r}, "
                        f"which is not registered in this ProcessorRegistry"
                    )

    def _compute_layers(self) -> dict[str, int]:
        """Kahn's algorithm over the depends_on DAG. layer(p) = 0 if
        `p.depends_on` is empty, else 1 + max(layer(d) for d in
        p.depends_on). Raises DependencyCycleError if the graph isn't
        acyclic."""
        in_degree: dict[str, int] = {p.id: len(p.depends_on) for p in self.processors}
        dependents: dict[str, list[str]] = defaultdict(list)
        for processor in self.processors:
            for dep_id in processor.depends_on:
                dependents[dep_id].append(processor.id)

        layer: dict[str, int] = {}
        remaining = dict(in_degree)
        queue: deque[str] = deque(pid for pid, deg in in_degree.items() if deg == 0)
        for pid in queue:
            layer[pid] = 0

        processed = 0
        while queue:
            pid = queue.popleft()
            processed += 1
            for dependent_id in dependents[pid]:
                remaining[dependent_id] -= 1
                layer[dependent_id] = max(layer.get(dependent_id, 0), layer[pid] + 1)
                if remaining[dependent_id] == 0:
                    queue.append(dependent_id)

        if processed != len(self.processors):
            stuck = sorted(pid for pid, deg in remaining.items() if deg > 0)
            raise DependencyCycleError(f"circular depends_on among processors: {stuck!r}")
        return layer

    def _compute_enabled(self) -> None:
        """Seed with every Identifier (always enabled - they're the
        consumers evidence ultimately flows to), then walk each
        enabled processor's own `depends_on` forward, marking
        everything reachable as enabled too. This is reachability, not
        "listed as a direct dependent of an Identifier" - see module
        docstring point 3."""
        enabled_ids: set[str] = {p.id for p in self.processors if isinstance(p, Identifier)}
        queue: deque[str] = deque(enabled_ids)
        while queue:
            pid = queue.popleft()
            for dep_id in self._by_id[pid].depends_on:
                if dep_id not in enabled_ids:
                    enabled_ids.add(dep_id)
                    queue.append(dep_id)

        for processor in self.processors:
            processor.enabled = processor.id in enabled_ids


def default_registry() -> ProcessorRegistry:
    """The processor/identifier set the engine ships with today.
    Import is local to avoid a hard dependency cycle (identifiers
    import from engine.core, not the other way around) and to make it
    obvious this is the one function to edit when adding/removing
    processors or identifiers."""
    from engine.identifiers.name_match import NameMatchIdentifier
    from engine.identifiers.qa_pattern import QuestionAnsweringPatternIdentifier
    from engine.identifiers.screenshare_heuristic import ScreenshareHeuristicIdentifier
    from engine.identifiers.speaking_share import SpeakingShareIdentifier

    return ProcessorRegistry(
        [
            NameMatchIdentifier(),
            SpeakingShareIdentifier(),
            QuestionAnsweringPatternIdentifier(),
            ScreenshareHeuristicIdentifier(),
        ]
    )
