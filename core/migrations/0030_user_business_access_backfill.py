"""
Backfill the UBA rows matching the links that already exist: CLAIM_ADMIN from the health
facility attached to a user, ENROLMENT from the villages of their enrolment officer.

Every user currently carrying an HF (through their claim admin, else through their
interactive user) gets one `CLAIM_ADMIN` row on it, and every OfficerVillage of their
enrolment officer becomes an `ENROLMENT` row.

This is a one way seeding. From here on the direction is reversed: UBA is what a client
sends, and `core.services.userServices` derives the claim admin entry and the officer
villages from the links.

This runs **in parallel** with the existing ClaimAdmin mechanism: the dedicated
`ClaimAdmin` entry is still created and removed as before, and nothing reading it changes
behaviour. The UBA rows are added next to it, not instead of it.

Note: rows are inserted in bulk through the historical model, so no
`HistoricalUserBusinessAccess` entry is written for the backfill itself. Subsequent
changes go through the service and are versioned normally.

Note: the health facility of a claim admin and the villages of an officer are read with raw
SQL. Both columns exist in the database but were never added by a claim / location
migration, so the historical models rebuilt here do not carry them and the ORM raises
FieldError on any traversal. See `_claim_admin_health_facilities` and `_officer_villages`.
"""
from django.db import migrations

CLAIM_ADMIN_UBA_LINK_TYPE = "CLAIM_ADMIN"
ENROLMENT_UBA_LINK_TYPE = "ENROLMENT"
HF_APP_LABEL = "location"
HF_MODEL = "healthfacility"
VILLAGE_MODEL = "location"
BATCH_SIZE = 1000


def _hf_content_type(apps):
    """
    The ContentType of location.HealthFacility, or None when location is not installed.
    get_or_create rather than get: on a fresh database the contenttypes post_migrate
    hook has not run yet, and django reuses the row we create here.
    """
    try:
        apps.get_model(HF_APP_LABEL, "HealthFacility")
    except LookupError:
        return None
    content_type_model = apps.get_model("contenttypes", "ContentType")
    content_type, _ = content_type_model.objects.get_or_create(
        app_label=HF_APP_LABEL, model=HF_MODEL
    )
    return content_type


def _village_content_type(apps):
    """The ContentType of location.Location, or None when location is not installed."""
    try:
        apps.get_model(HF_APP_LABEL, "Location")
    except LookupError:
        return None
    content_type_model = apps.get_model("contenttypes", "ContentType")
    content_type, _ = content_type_model.objects.get_or_create(
        app_label=HF_APP_LABEL, model=VILLAGE_MODEL
    )
    return content_type


def _officer_villages(apps, schema_editor, officer_ids):
    """
    `[(officer_id, location_id)]` for the still valid officer villages of `officer_ids`.

    Raw SQL for the same reason as the claim admin lookup: the historical
    `location.OfficerVillage` rebuilt here only carries id / legacy_id / validity / audit,
    its `officer` and `location` foreign keys were never added by a location migration, so
    the ORM cannot see the OfficerId and LocationId columns that do exist in the database.
    """
    if not officer_ids:
        return []
    try:
        officer_village_model = apps.get_model(HF_APP_LABEL, "OfficerVillage")
    except LookupError:
        return []

    quote = schema_editor.connection.ops.quote_name
    table = quote(officer_village_model._meta.db_table)
    validity_column = quote(officer_village_model._meta.get_field("validity_to").column)
    # db_columns of OfficerVillage.officer / .location, absent from the migration state
    officer_column = quote("OfficerId")
    location_column = quote("LocationId")

    rows = []
    officer_ids = list(officer_ids)
    with schema_editor.connection.cursor() as cursor:
        for start in range(0, len(officer_ids), BATCH_SIZE):
            chunk = officer_ids[start:start + BATCH_SIZE]
            placeholders = ", ".join(["%s"] * len(chunk))
            cursor.execute(
                f"SELECT {officer_column}, {location_column} FROM {table} "
                f"WHERE {validity_column} IS NULL AND {location_column} IS NOT NULL "
                f"AND {officer_column} IN ({placeholders})",
                chunk,
            )
            rows.extend(cursor.fetchall())
    return rows


def create_enrolment_business_accesses(apps, schema_editor):
    """One ENROLMENT link per OfficerVillage row, for the users holding that officer."""
    content_type = _village_content_type(apps)
    if content_type is None:
        return

    user_model = apps.get_model("core", "User")
    business_access_model = apps.get_model("core", "UserBusinessAccess")

    already_linked = set(
        business_access_model.objects.filter(
            link_type=ENROLMENT_UBA_LINK_TYPE, content_type=content_type
        ).values_list("user_id", "object_id")
    )
    users_by_officer = {}
    for user_id, officer_id in user_model.objects.filter(
        validity_to__isnull=True, officer__isnull=False
    ).values_list("id", "officer_id"):
        users_by_officer.setdefault(officer_id, []).append(user_id)

    rows = []
    for officer_id, location_id in _officer_villages(apps, schema_editor, users_by_officer.keys()):
        for user_id in users_by_officer.get(officer_id, ()):
            if (user_id, str(location_id)) in already_linked:
                continue
            already_linked.add((user_id, str(location_id)))
            rows.append(
                business_access_model(
                    user_id=user_id,
                    link_type=ENROLMENT_UBA_LINK_TYPE,
                    content_type=content_type,
                    object_id=str(location_id),
                )
            )

    if rows:
        business_access_model.objects.bulk_create(rows, batch_size=BATCH_SIZE)


def delete_enrolment_business_accesses(apps, schema_editor):
    content_type = _village_content_type(apps)
    if content_type is None:
        return
    business_access_model = apps.get_model("core", "UserBusinessAccess")
    business_access_model.objects.filter(
        link_type=ENROLMENT_UBA_LINK_TYPE, content_type=content_type
    ).delete()


def _claim_admin_health_facilities(apps, schema_editor):
    """
    `{claim_admin_id: health_facility_id}` for the claim admins still valid.

    Read with raw SQL on purpose: `claim.ClaimAdmin.health_facility` exists on the model but
    **no claim migration ever added it**, so the historical model rebuilt here has no such
    field and any ORM traversal of `claim_admin__health_facility_id` raises FieldError. The
    column is there in the database, only the migration state is missing it, so reading it
    directly keeps the backfill correct without depending on claim's broken state.
    """
    try:
        claim_admin_model = apps.get_model("claim", "ClaimAdmin")
    except LookupError:
        return {}

    quote = schema_editor.connection.ops.quote_name
    table = quote(claim_admin_model._meta.db_table)
    pk_column = quote(claim_admin_model._meta.pk.column)
    validity_column = quote(claim_admin_model._meta.get_field("validity_to").column)
    # HFId is the db_column of ClaimAdmin.health_facility, not in the migration state
    hf_column = quote("HFId")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {pk_column}, {hf_column} FROM {table} "
            f"WHERE {validity_column} IS NULL AND {hf_column} IS NOT NULL"
        )
        return {row[0]: row[1] for row in cursor.fetchall()}


def create_claim_admin_business_accesses(apps, schema_editor):
    content_type = _hf_content_type(apps)
    if content_type is None:
        return

    user_model = apps.get_model("core", "User")
    business_access_model = apps.get_model("core", "UserBusinessAccess")

    already_linked = set(
        business_access_model.objects.filter(
            link_type=CLAIM_ADMIN_UBA_LINK_TYPE, content_type=content_type
        ).values_list("user_id", "object_id")
    )
    claim_admin_hfs = _claim_admin_health_facilities(apps, schema_editor)

    rows = []
    users = user_model.objects.filter(validity_to__isnull=True).values_list(
        "id",
        "claim_admin_id",
        "i_user__health_facility_id",
        "i_user__validity_to",
    )
    for user_id, claim_admin_id, i_hf_id, i_validity_to in users.iterator():
        # mirrors User.get_health_facility(): the claim admin wins over the interactive user
        ca_hf_id = claim_admin_hfs.get(claim_admin_id) if claim_admin_id else None
        if ca_hf_id:
            hf_id = ca_hf_id
        elif i_hf_id and i_validity_to is None:
            hf_id = i_hf_id
        else:
            continue
        if (user_id, str(hf_id)) in already_linked:
            continue
        already_linked.add((user_id, str(hf_id)))
        rows.append(
            business_access_model(
                user_id=user_id,
                link_type=CLAIM_ADMIN_UBA_LINK_TYPE,
                content_type=content_type,
                object_id=str(hf_id),
            )
        )

    if rows:
        business_access_model.objects.bulk_create(rows, batch_size=BATCH_SIZE)


def delete_claim_admin_business_accesses(apps, schema_editor):
    content_type = _hf_content_type(apps)
    if content_type is None:
        return
    business_access_model = apps.get_model("core", "UserBusinessAccess")
    business_access_model.objects.filter(
        link_type=CLAIM_ADMIN_UBA_LINK_TYPE, content_type=content_type
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('core', '0029_roleright_uba_userbusinessaccess'),
    ]

    operations = [
        migrations.RunPython(
            create_claim_admin_business_accesses,
            delete_claim_admin_business_accesses,
        ),
        migrations.RunPython(
            create_enrolment_business_accesses,
            delete_enrolment_business_accesses,
        ),
    ]
