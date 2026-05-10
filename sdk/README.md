# lumid-data-sdk

HTTP SDK for the lumid.data data plane. Sync + async clients over the
unified URL — `db_*`, `storage_*`, `sql`, `agent_run`, `retrieve`,
`healthz`, `storage_stat`.

## Install

```bash
pip install "lumid-data-sdk @ git+https://github.com/mlsys-io/lumid.data.git#subdirectory=sdk"
```

## Usage

```python
from lumid_data.sdk import Client, AsyncClient

c = Client(base_url="http://localhost:9100", token=os.environ.get("LUMID_TOKEN"))
rows = c.sql("SELECT 1").rows
c.storage_put("photos", "cat.png", open("cat.png", "rb").read(), mime="image/png")

ac = AsyncClient(base_url="http://localhost:9100", token=os.environ.get("LUMID_TOKEN"))
rows = (await ac.sql("SELECT 1")).rows
```

`Client` and `AsyncClient` share the same surface — pick whichever
matches your event-loop posture. See lumid.data's main repo for the
underlying REST API.
