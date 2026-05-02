"""In-process Python client for lumid apps.

Apps import this and call ``client.ingest(...)`` / ``client.read_table(...)``
from their ``commands/<verb>.py`` files. The client is plain HTTP for
the data plane API; reads bypass the API and go straight to UC -> Delta
for low-latency scans (large or governed reads should go through
FlowMesh ``data_retrieval(type=delta)`` once that lands).

Phase 1 surface:
- ``read_table(dataset_id)`` / ``preview(dataset_id, n)``
- ``ingest(source_id, payload, *, mime=None)``
- ``creds(app_name)``
- ``subscribe("DatasetReady")``  (async iterator)
"""

from .core import Client, ClientError, Credentials

__all__ = ["Client", "ClientError", "Credentials"]
