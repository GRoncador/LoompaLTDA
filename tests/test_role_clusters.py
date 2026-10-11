"""Every agent that talks to the router names a cluster on purpose: an unknown role falls into
`routine`, the cheapest one, without any error."""

import importlib
import inspect
import pkgutil

import loompa.agents as agents_pkg
from loompa.agents.base import LoompaAgent
from loompa.agents.support import PROMPTS
from loompa.config.schema import ROLE_CLUSTERS

# roles that never call the router (they record, schedule or use their own model)
NO_ROUTER = {"kaizen", "ops"}


def test_every_agent_role_has_a_cluster():
    roles = set(PROMPTS)
    for mod in pkgutil.iter_modules(agents_pkg.__path__):
        m = importlib.import_module(f"loompa.agents.{mod.name}")
        for _, cls in inspect.getmembers(m, inspect.isclass):
            if issubclass(cls, LoompaAgent) and isinstance(cls.__dict__.get("role"), str):
                roles.add(cls.__dict__["role"])
    missing = sorted(roles - NO_ROUTER - set(ROLE_CLUSTERS))
    assert not missing, f"roles with no cluster: {missing}"
