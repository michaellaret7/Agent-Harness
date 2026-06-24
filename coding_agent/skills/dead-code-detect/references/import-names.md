# Package Name → Import Name Mismatches

When scanning for unused dependencies, the pip-install name often differs from the Python import name. Use this table to avoid false positives.

## Common mismatches

| Package (pip) | Import name |
|---------------|-------------|
| `Pillow` | `PIL` |
| `PyYAML` | `yaml` |
| `python-dateutil` | `dateutil` |
| `python-dotenv` | `dotenv` |
| `scikit-learn` | `sklearn` |
| `beautifulsoup4` | `bs4` |
| `pymongo` | `pymongo` |
| `python-jose` | `jose` |
| `python-multipart` | `multipart` |
| `uvicorn` | `uvicorn` |
| `pydantic-settings` | `pydantic_settings` |
| `typing-extensions` | `typing_extensions` |
| `attrs` | `attr` or `attrs` |
| `opencv-python` | `cv2` |
| `scikit-image` | `skimage` |
| `matplotlib` | `matplotlib` |
| `seaborn` | `seaborn` |
| `pandas` | `pandas` or `pd` |
| `numpy` | `numpy` or `np` |
| `tables` | `tables` or `pt` |
| `xlrd` | `xlrd` |
| `openpyxl` | `openpyxl` |
| `msgpack-python` | `msgpack` |
| `mysql-connector-python` | `mysql.connector` |
| `psycopg2` or `psycopg2-binary` | `psycopg2` |
| `pymysql` | `pymysql` |
| `cryptography` | `cryptography` |
| `pycryptodome` | `Crypto` |
| `google-auth` | `google.auth` |
| `google-cloud-storage` | `google.cloud.storage` |
| `boto3` | `boto3` |
| `azure-storage-blob` | `azure.storage.blob` |

## Detecting mismatches programmatically

When uncertain, check the package metadata:

```python
# Get actual import names from an installed package
import importlib.metadata
dist = importlib.metadata.distribution("package-name")
top_level = dist.read_text("top_level.txt")  # newline-separated list of importable names
```

This reads `top_level.txt` from the package's `.dist-info`, which lists the actual importable top-level names.

## Framework-discovered code (skip these in orphan scans)

Certain frameworks load code by convention rather than explicit import:

| Framework | Auto-discovery |
|-----------|---------------|
| Django | `models.py`, `views.py`, `admin.py`, `apps.py`, `urls.py`, `signals.py` |
| Flask | `@app.route`, `@blueprint.route` |
| FastAPI | `@router.*` decorated functions |
| Celery | `@shared_task`, `@app.task` |
| Pytest | `conftest.py`, `@pytest.fixture`, `test_*.py` |
| Sphinx | `conf.py`, extensions in `extensions` list |
| Click | `@click.command()`, `@cli.command()` |
| Typer | `@app.command()` |
| Airflow | DAG files in `dags/` directory |
| Prefect | `@flow`, `@task` decorated functions |
