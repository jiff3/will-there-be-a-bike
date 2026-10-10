"""Schema rollback must not remove shared database capabilities."""

import runpy
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_base_downgrade_preserves_provider_extensions() -> None:
    migration = runpy.run_path(
        str(Path(__file__).parents[1] / "alembic/versions/20261009_0001_initial.py")
    )
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration["downgrade"]()
    sql = output.getvalue()
    assert "DROP TABLE stations" in sql
    assert "DROP TABLE bike_share_systems" in sql
    assert "EXTENSION" not in sql
    assert "CASCADE" not in sql
