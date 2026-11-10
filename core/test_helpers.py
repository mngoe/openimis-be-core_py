from core.models import Officer, InteractiveUser, User, TechnicalUser, filter_validity
from core.models import Role, RoleRight, UserBusinessAccess
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase
from core.services.userServices import create_or_update_officer_villages
from core.services import create_or_update_user_roles
from location.models import Location
from  uuid import uuid4
import json
import time

def create_test_officer(valid=True, custom_props={}, villages = []):

    code = custom_props.pop('code', None)
    uuid = custom_props.pop('uuid', None)
    qs_eo = Officer.objects
    eo = None
    data = {
            "code":  code or "TSTOFF",
            "uuid":uuid,
            "last_name": "Officer",
            "other_names": "Test",
            "validity_to": None if valid else "2019-06-01",
            "audit_user_id": -1,
            "phone": "0000110100",
            **(custom_props if custom_props else {})
        }
    if code:
        qs_eo = qs_eo.filter(code=code)
    if uuid:
        qs_eo = qs_eo.filter(uuid=uuid)
    eo = None        
    if code or uuid:
        eo = qs_eo.first()
    if eo:
        data['uuid']=eo.uuid
        eo.update(data)
    else:
        data['uuid']=uuid4()
        eo =   Officer.objects.create(**data)
    if villages == []:
        villages == Location.objects.filter(*filter_validity(), type = 'V').first()
    if eo:
        result = create_or_update_officer_villages(eo, [v.id for v in villages], 1)
        return eo

def create_test_role(name="TestRole", rights=None, uba_rights=None, custom_props=None):
    """
    Create a role with its two right bags: `rights` are global (RoleRight.uba = False),
    `uba_rights` are only granted on the business objects the user is linked to
    through a UserBusinessAccess row (RoleRight.uba = True).
    """
    role = Role.objects.create(
        **{
            "name": name,
            "is_system": 0,
            "is_blocked": False,
            "audit_user_id": -1,
            **(custom_props if custom_props else {})
        }
    )
    for uba, bag in ((False, rights), (True, uba_rights)):
        for right_id in (bag or []):
            RoleRight.objects.create(
                role=role,
                right_id=right_id,
                uba=uba,
                audit_user_id=-1,
            )
    return role


def get_or_create_test_admin_role(name="TestIMISAdmin"):
    """
    An IMIS Administrator role (`is_system=64`) created by the test itself: the user
    holding it is a superuser, so the tests do not depend on the system roles of a
    seeded database.
    """
    role = Role.objects.filter(name=name, is_system=64, validity_to__isnull=True).first()
    return role if role else create_test_role(name=name, custom_props={"is_system": 64})


def create_imis_admin_role():
    """Same name as the 26.10 helper; the administrator role is found or created."""
    return get_or_create_test_admin_role()


def resolve_perm_right_ids(perm_names):
    """The integer right ids behind permission names as declared in the modules' DEFAULT_CFG."""
    from core.utils import collect_all_gql_permissions

    flat_perms = {}
    for app_perms in collect_all_gql_permissions().values():
        flat_perms.update(app_perms)
    right_ids = set()
    for perm_name in perm_names:
        if perm_name not in flat_perms:
            raise Exception(f"Permission {perm_name} not found")
        right_ids.update(int(pid) for pid in flat_perms[perm_name])
    return sorted(right_ids)


def _sync_role_rights(role, right_ids):
    """Make the role's global rights (uba=False) exactly `right_ids`; its UBA rights are left alone."""
    from django.core.cache import cache

    current = RoleRight.objects.filter(role=role, uba=False, validity_to__isnull=True)
    if set(current.values_list("right_id", flat=True)) == set(right_ids):
        return
    current.delete()
    for right_id in right_ids:
        RoleRight.objects.create(role=role, right_id=right_id, uba=False, audit_user_id=-1)
    # the rights of a user are cached
    cache.clear()


def get_or_create_test_role(perm_names=(), name="TestRole", is_system=0, custom_props=None):
    """
    The role named `name`, created when missing, holding exactly the rights behind
    `perm_names` (permission names as declared in the modules' DEFAULT_CFG).

    Ported from 26.10 (`create_test_role` there): tests ask for a role by what it
    may do rather than by the id it has in a seeded demo database, so they run on
    an empty test database too.
    """
    role = Role.objects.filter(name=name, validity_to__isnull=True).first()
    if role is None:
        role = Role.objects.create(**{
            "name": name,
            "is_system": is_system,
            "is_blocked": False,
            "audit_user_id": -1,
            **(custom_props if custom_props else {}),
        })
    _sync_role_rights(role, resolve_perm_right_ids(perm_names))
    return role


def create_enrolment_officer_role():
    """The Enrolment Officer role of the demo fixture (role id 1 there), as in 26.10."""
    return get_or_create_test_role([
        "gql_query_report_perms",
        "gql_query_insuree_perms",
        "gql_query_insurees_perms",
        "gql_mutation_update_insurees_perms",
        "gql_mutation_create_insurees_perms",
        "gql_mutation_delete_insurees_perms",
        "gql_query_locations_perms",
        "gql_query_products_perms",
        "gql_query_policies_perms",
        "gql_mutation_create_policies_perms",
        "gql_mutation_edit_policies_perms",
        "gql_mutation_renew_policies_perms",
        "gql_mutation_suspend_policies_perms",
        "gql_mutation_delete_policies_perms",
        "gql_query_premiums_perms",
        "gql_mutation_create_premiums_perms",
        "gql_mutation_update_premiums_perms",
        "gql_mutation_delete_premiums_perms",
        "gql_query_payers_perms",
    ], name="EnrolmentOfficer", is_system=1)


def create_claim_admin_role():
    """The Claim Administrator role of the demo fixture (role id 9 there), as in 26.10."""
    return get_or_create_test_role([
        "gql_query_locations_perms",
        "gql_query_policies_perms",
        "gql_query_insuree_perms",
        "gql_query_insurees_perms",
        "gql_mutation_create_claims_perms",
        "gql_mutation_update_claims_perms",
        "gql_query_claims_perms",
        "gql_mutation_load_claims_perms",
        "gql_mutation_restore_claims_perms",
        "gql_mutation_delete_claims_perms",
        "gql_query_health_facilities_perms",
        "gql_query_medical_services_perms",
        "gql_query_medical_items_perms",
    ], name="ClaimAdministrator", is_system=256)


def create_medical_officer_role():
    """The Medical Officer role of the demo fixture (role id 5 there), as in 26.10."""
    return get_or_create_test_role([
        "gql_query_report_perms",
        "gql_query_policies_perms",
        "gql_query_eligibilities_perms",
        "gql_query_claims_perms",
        "gql_mutation_create_claims_perms",
        "gql_mutation_update_claims_perms",
        "gql_mutation_submit_claims_perms",
        "gql_mutation_select_claim_feedback_perms",
        "gql_mutation_bypass_claim_feedback_perms",
        "gql_mutation_skip_claim_feedback_perms",
        "gql_mutation_deliver_claim_feedback_perms",
        "gql_mutation_select_claim_review_perms",
        "gql_mutation_bypass_claim_review_perms",
        "gql_mutation_skip_claim_review_perms",
        "gql_mutation_deliver_claim_review_perms",
        "gql_mutation_process_claims_perms",
        "gql_reports_claim_history_report_perms",
    ], name="MedicalOfficer", is_system=16)


def create_test_user_business_access(user, business_object, link_type, audit_user=None, custom_props=None):
    """
    Link a user to a business object under a credential, `link_type` being a code
    registered through `core.uba_link_types` (not an openIMIS role).
    """
    from django.contrib.contenttypes.models import ContentType

    business_access = UserBusinessAccess(
        **{
            "user": user,
            "link_type": link_type,
            "content_type": ContentType.objects.get_for_model(business_object),
            "object_id": str(business_object.pk),
            **(custom_props if custom_props else {})
        }
    )
    business_access.save(user=audit_user)
    return business_access


def create_test_interactive_user(username='TestInteractiveTest', password="Test1234", roles=None, custom_props=None):
    if roles is None:
        roles = [get_or_create_test_admin_role().id]
    i_user = InteractiveUser.objects.create(
        **{
            "language_id": "en",
            "last_name": "TestLastName",
            "other_names": "Test Other Names",
            "login_name": username,
            "audit_user_id": -1,
            "role_id": roles[0],
            **(custom_props if custom_props else {})
        }
    )
    i_user.set_password(password)
    i_user.save()
    create_or_update_user_roles(i_user, roles, None)
    return User.objects.create(
        username=username,
        i_user=i_user,
    )


def create_test_technical_user(
        username='TestAdminTechnicalTest', password="S\/pe®Pąßw0rd""", super_user=False,
        custom_tech_user_props={}, custom_core_user_props={}):
    custom_tech_user_props['password'] = password
    t_user, t_user_created = TechnicalUser.objects.get_or_create(
        **{
            "username": username,
            "email": "test_tech_user@openimis.org",
            "is_staff": super_user,
            "is_superuser": super_user,
            **(custom_tech_user_props)
        }
    )
    # Just for safety and retrieving the User because TechnicalUser will automatically create its User
    custom_core_user_props['password'] = password
    core_user, core_user_created = User.objects.get_or_create(
        username=username,
        t_user=t_user,
        **(custom_core_user_props)
    )
    return core_user

def compare_dicts(dict1, dict2):
    def recursive_compare(obj1, obj2):
        if isinstance(obj1, dict) and isinstance(obj2, dict):
            # Check keys
            if set(obj1.keys()) != set(obj2.keys()):
                return False

            # Recursively compare values
            for key in obj1.keys():
                if not recursive_compare(obj1[key], obj2[key]):
                    return False

            return True
        elif isinstance(obj1, list) and isinstance(obj2, list):
            # Check list length
            if len(obj1) != len(obj2):
                return False

            # Recursively compare list elements
            for item1, item2 in zip(obj1, obj2):
                if not recursive_compare(item1, item2):
                    return False

            return True
        elif isinstance(obj1 , (float, int)) or (isinstance(obj1, str) and obj1.isnumeric()) \
                and isinstance(obj2 , (float, int)) or (isinstance(obj2, str) and obj2.isnumeric()):
            # Compare floating-point numbers with a tolerance for decimal precision
            return round(float(obj1), 2) == round(float(obj2), 2)

        # Compare other types directly
        return obj1 == obj2

    return recursive_compare(dict1, dict2)



def AssertMutation(test_obj,mutation_uuid, token ):
    return openIMISGraphQLTestCase().get_mutation_result(mutation_uuid, token)


# ---------------------------------------------------------------------------------------
# Security parity helpers (ported/adapted from the openimis-dev-tools reference:
# core.test_helpers.create_right_only_user and location.test_helpers.create_basic_test_locations)
# ---------------------------------------------------------------------------------------

class SecurityTree:
    """Two disjoint branches R1>D1>W1>V1 and R2>D2>W2>V2 with a health facility in each
    district. A 'restricted' user is granted D1 only; every object built on branch 2 must
    be invisible / untouchable for that user."""
    pass


def create_security_tree(prefix="SP"):
    """Build the two-branch location tree. `prefix` (max 4 chars) keeps codes unique."""
    from location.models import HealthFacility, HealthFacilityLegalForm
    tree = SecurityTree()
    for n in ("1", "2"):
        r = Location.objects.create(code=f"{prefix}R{n}", name=f"{prefix} Region {n}", type="R",
                                    audit_user_id=-1, validity_from="2019-01-01")
        d = Location.objects.create(code=f"{prefix}D{n}", name=f"{prefix} District {n}", type="D",
                                    parent=r, audit_user_id=-1, validity_from="2019-01-01")
        w = Location.objects.create(code=f"{prefix}W{n}", name=f"{prefix} Ward {n}", type="W",
                                    parent=d, audit_user_id=-1, validity_from="2019-01-01")
        v = Location.objects.create(code=f"{prefix}V{n}", name=f"{prefix} Village {n}", type="V",
                                    parent=w, audit_user_id=-1, validity_from="2019-01-01")
        hf = HealthFacility.objects.create(
            code=f"{prefix}HF{n}", name=f"{prefix} HF {n}", level="H", care_type="B",
            legal_form=HealthFacilityLegalForm.objects.filter(code="C").first()
            or HealthFacilityLegalForm.objects.first(),
            location=d, validity_from="2019-01-01", audit_user_id=-1, offline=False)
        for k, val in (("r", r), ("d", d), ("w", w), ("v", v), ("hf", hf)):
            setattr(tree, f"{k}{n}", val)
    return tree


def create_right_only_user(username, rights, district_ids=(), uba_rights=None, custom_props=None):
    """A NON-superuser holding exactly `rights` (right ids) in the global bag and assigned
    to `district_ids`. Mirrors the reference `create_right_only_user`."""
    from core.services.userServices import create_or_update_user_districts
    from django.core.cache import cache
    role = create_test_role(name=f"SP role {username}", rights=list(rights or []),
                            uba_rights=uba_rights)
    user = create_test_interactive_user(username=username, roles=[role.id],
                                        custom_props=custom_props)
    if district_ids:
        create_or_update_user_districts(user.i_user, list(district_ids), -1)
    cache.clear()
    return user


def get_test_token(user):
    """JWT for HTTP GraphQL / REST calls (Authorization: Bearer <token>)."""
    from dataclasses import dataclass
    from graphql_jwt.shortcuts import get_token

    @dataclass
    class _Ctx:
        user: object

    return get_token(user, _Ctx(user=user))


def perm_ids(perms):
    """Config right lists are strings or ints; normalise to ints."""
    return [int(p) for p in (perms or [])]




# ---------------------------------------------------------------------------------------
# OP-2029 contract helpers (ported from the openIMIS reference, feature/OP-3317)
# ---------------------------------------------------------------------------------------

def mark_deleted(obj):
    """Mark ``obj`` deleted the way its model family does, without side effects.

    Written straight to the row, so no audit copy or history entry is made:
    a test of the validity filter wants exactly one deleted row.
    """
    import datetime
    model = type(obj)
    fields = {f.name for f in model._meta.fields}
    changes = {}
    if "is_deleted" in fields:
        changes["is_deleted"] = True
    if "active" in fields:
        changes["active"] = False
    if "validity_to" in fields:
        changes["validity_to"] = datetime.datetime.now()
    model.objects.filter(pk=obj.pk).update(**changes)
    # update() bypasses save(), so a cached copy would still read as live
    if hasattr(obj, "delete_cache"):
        obj.delete_cache()
    return obj


def assert_row_filters(test, model, queryset, user, live, deleted, default_hides_deleted):
    """The OP-2029 validity contract of ``model.get_queryset``, on two rows.

    ``live`` and ``deleted`` are primary keys inside ``queryset``.
    ``default_hides_deleted`` states what a caller asking for nothing gets
    today, which phase 1 of OP-3317 must not change.
    """
    from core.models.row_filters import Validity

    def pks(**kwargs):
        rows = model.get_queryset(queryset, user, **kwargs)
        return set(rows.values_list("pk", flat=True)) & {live, deleted}

    test.assertEqual({live} if default_hides_deleted else {live, deleted}, pks())
    test.assertEqual({live}, pks(validity=Validity.NOW))
    test.assertEqual({deleted}, pks(validity=Validity.DELETED_NOW))
    test.assertEqual({live, deleted}, pks(validity=Validity.ALL))
