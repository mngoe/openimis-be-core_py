"""
Grant the UBA rights to every role that already administers users.

Managing the links of a user is part of administering that user, so a role holding a user
right gets the matching UBA one: whoever can search users can search their links, whoever
can create users can create links, and so on.

The rights land in the **global** bag (`uba=False`): administering the links is an ordinary
global action, it is not itself scoped to a business object.
"""
from django.db import migrations

# user right -> UBA right, see core.apps.DJANGO_PERMS
USER_TO_UBA_RIGHT = {
    121701: 122501,  # query
    121702: 122502,  # create
    121703: 122503,  # update, also covers activate / deactivate
    121704: 122504,  # delete
}


def add_uba_rights(apps, schema_editor):
    role_right_model = apps.get_model("core", "RoleRight")

    valid = role_right_model.objects.filter(validity_to__isnull=True)
    # (role_id, right_id) already present in the global bag, so the run is idempotent and
    # the (role, right_id, uba) unique constraint on valid rows holds
    existing = set(valid.filter(uba=False).values_list("role_id", "right_id"))

    rows = []
    for role_id, user_right, audit_user_id in valid.filter(
        right_id__in=USER_TO_UBA_RIGHT.keys(), uba=False
    ).values_list("role_id", "right_id", "audit_user_id"):
        uba_right = USER_TO_UBA_RIGHT[user_right]
        if (role_id, uba_right) in existing:
            continue
        existing.add((role_id, uba_right))
        rows.append(
            role_right_model(
                role_id=role_id,
                right_id=uba_right,
                uba=False,
                audit_user_id=audit_user_id,
            )
        )

    if rows:
        role_right_model.objects.bulk_create(rows, batch_size=1000)


def remove_uba_rights(apps, schema_editor):
    role_right_model = apps.get_model("core", "RoleRight")
    role_right_model.objects.filter(
        right_id__in=USER_TO_UBA_RIGHT.values(), uba=False
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0030_user_business_access_backfill'),
    ]

    operations = [
        migrations.RunPython(add_uba_rights, remove_uba_rights),
    ]
