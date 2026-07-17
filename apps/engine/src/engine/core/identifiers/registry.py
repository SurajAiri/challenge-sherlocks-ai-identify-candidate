"""
Back-compat shim. The real implementation moved to
`engine.core.registry` (`ProcessorRegistry`) since the registry now
manages Processors in general, not just Identifiers - see that
module's docstring for the dependency-layering/`enabled` logic this
used to not have.

`IdentifierRegistry` is kept as a plain alias (not a subclass) so
existing imports/call sites - including tests that construct one
directly with just a list of Identifiers, e.g.
`IdentifierRegistry([SomeIdentifier()])` - keep working unchanged:
a list of only Identifiers is a valid (degenerate, all-layer-0 unless
they cross-depend) ProcessorRegistry.
"""

from __future__ import annotations

from engine.core.registry import ProcessorRegistry, default_registry

IdentifierRegistry = ProcessorRegistry

__all__ = ["IdentifierRegistry", "default_registry"]
