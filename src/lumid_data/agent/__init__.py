"""The data agent: bounded data-engineering agent in the four-stage loop
(Perception -> Planning -> Grounding -> Execution + Reflection).

Lineage: Sun et al., Autonomous Data Agents (arXiv 2509.18710);
Dataforge (arXiv 2511.06185).
"""

from . import decisions, modality, quality, schema
from .router import AgentResult, route

__all__ = ["AgentResult", "decisions", "modality", "quality", "route", "schema"]
