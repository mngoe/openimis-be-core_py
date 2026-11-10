"""Schema the live models need before *other* apps' migrations run.

Data migrations across the modules write role rights through the live
``RoleRight`` model (``core.utils.insert_role_right_for_system`` resolves it from
the app registry, not from the migration state). Since UBA, that model carries a
``uba`` field, so those inserts select and write ``tblRoleRight.UBA``.

``migrate`` plans apps alphabetically: on a fresh database api_fhir_r4,
calculation, claim, contract, ... run their right migrations before core's
0029 adds the column, and fail with 'column tblRoleRight.UBA does not exist'.
Dependencies cannot fix that order -- many of those modules are installed from
PyPI. So the column is added once, before any migration runs, and 0029 adds it
only when it is still missing.
"""
import logging

from django.db import connections

logger = logging.getLogger(__name__)

TABLE = "tblRoleRight"
COLUMN = "UBA"


def _sql(vendor):
    if vendor == "postgresql":
        return f'ALTER TABLE "{TABLE}" ADD COLUMN IF NOT EXISTS "{COLUMN}" boolean NOT NULL DEFAULT false'
    if vendor == "microsoft":
        return (
            f"IF COL_LENGTH('{TABLE}', '{COLUMN}') IS NULL "
            f"ALTER TABLE [{TABLE}] ADD [{COLUMN}] bit NOT NULL "
            f"CONSTRAINT [DF_{TABLE}_{COLUMN}] DEFAULT 0"
        )
    return None


def ensure_roleright_uba_column(using="default"):
    """Add tblRoleRight.UBA when the table exists without it. Idempotent."""
    connection = connections[using]
    sql = _sql(connection.vendor)
    if sql is None:
        return False
    with connection.cursor() as cursor:
        if TABLE not in connection.introspection.table_names(cursor):
            # an empty database: core's migrations create the table, column included
            return False
        cursor.execute(sql)
    return True


def ensure_roleright_uba_column_pre_migrate(sender, using="default", **kwargs):
    """pre_migrate receiver, connected for the core app only (it fires once per app)."""
    try:
        ensure_roleright_uba_column(using)
    except Exception as exc:  # never block a migrate on the guard itself
        logger.warning("Could not ensure %s.%s before migrating: %s", TABLE, COLUMN, exc)


def add_uba_column_if_missing(apps, schema_editor):
    """RunPython body of core 0029: the column may already exist (see above)."""
    ensure_roleright_uba_column(schema_editor.connection.alias)
