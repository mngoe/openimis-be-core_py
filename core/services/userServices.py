import logging
from gettext import gettext as _

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail, BadHeaderError
from django.template import loader
from django.utils.http import urlencode
from django.core.cache import cache

from django.contrib.auth import authenticate
from rest_framework import exceptions
from core.apps import CoreConfig
from core.models import User, InteractiveUser, Officer, UserRole, UserBusinessAccess
from core.models.user_business_access import resolve_business_content_type
from core.uba_link_types import is_valid_for
from core.validation.obligatoryFieldValidation import validate_payload_for_obligatory_fields

from program import models as program_models

logger = logging.getLogger(__file__)


def create_or_update_interactive_user(user_id, data, audit_user_id, connected):
    i_fields = {
        "username": "login_name",
        "other_names": "other_names",
        "last_name": "last_name",
        "phone": "phone",
        "email": "email",
        "language": "language_id",
        "health_facility_id": "health_facility_id",
    }
    data_subset = {v: data.get(k) for k, v in i_fields.items()}
    data_subset["audit_user_id"] = audit_user_id
    data_subset["role_id"] = data["roles"][0]  # The actual roles are stored in their own table
    data_subset["is_associated"] = connected
    if user_id:
        # TODO we might want to update a user that has been deleted. Use Legacy ID ?
        i_user = InteractiveUser.objects.filter(validity_to__isnull=True, user__id=user_id).first()
        if i_user.validity_to is not None and i_user.validity_to:
            raise ValidationError(_('core.user.edit_historical_data_error'))
    else:
        i_user = InteractiveUser.objects.filter(
            validity_to__isnull=True,
            login_name=data_subset["login_name"]
        ).first()    
    if i_user:
        i_user.save_history()
        [setattr(i_user, k, v) for k, v in data_subset.items()]
        if "password" in data:
            i_user.set_password(data["password"])
        created = False
    else:
        i_user = InteractiveUser(**data_subset)
        if "password" in data:
            i_user.set_password(data["password"])
        else:
            # No password provided for creation, will have to be set later.
            i_user.stored_password = CoreConfig.locked_user_password_hash
        created = True

    i_user.save()
    create_or_update_user_roles(i_user, data["roles"], audit_user_id)
    if "districts" in data:
        create_or_update_user_districts(
            i_user, data["districts"], data_subset["audit_user_id"]
        )
    if "programs" in data:
        programs = program_models.Program.objects.filter(idProgram__in=data["programs"])
        for program in programs:
            program.user.add(i_user)
    return i_user, created


def create_or_update_user_roles(i_user, role_ids, audit_user_id):
    from core import datetime

    now = datetime.datetime.now()
    UserRole.objects.filter(user=i_user, validity_to__isnull=True).update(
        validity_to=now
    )
    for role_id in role_ids:
        UserRole.objects.create(
            user=i_user, role_id=role_id, audit_user_id=audit_user_id
        )
    # Redundant as soon as role_ids holds something, the creates above each fire the
    # post_save receiver. It is the only invalidation when role_ids is empty: stripping
    # every role only runs the .update() closing the rows, which fires no receiver.
    from core.receivers import clear_user_rights_cache

    clear_user_rights_cache(i_user.id)



# TODO move to location module ?
def create_or_update_user_districts(i_user, district_ids, audit_user_id):
    # To avoid a static dependency from Core to Location, we'll dynamically load this class
    user_district_class = apps.get_model("location", "UserDistrict")
    from core import datetime

    now = datetime.datetime.now()
    user_district_class.objects.filter(user=i_user, validity_to__isnull=True).update(
        validity_to=now.to_ad_datetime()
    )
    for district_id in district_ids:
        user_district_class.objects.update_or_create(
            user=i_user,
            location_id=district_id,
            defaults={"validity_to": None, "audit_user_id": audit_user_id},
        )
    cache.delete('q_allowed_locations_'+str(i_user.id))



def create_or_update_officer_villages(officer, village_ids, audit_user_id):
    # To avoid a static dependency from Core to Location, we'll dynamically load this class
    officer_village_class = apps.get_model("location", "OfficerVillage")
    from core import datetime

    now = datetime.datetime.now()
    officer_village_class.objects.filter(
        officer=officer, validity_to__isnull=True
    ).update(validity_to=now)
    for village_id in village_ids:
        officer_village_class.objects.update_or_create(
            officer=officer,
            location_id=village_id,
            defaults={"validity_to": None, "audit_user_id": audit_user_id},
        )


@validate_payload_for_obligatory_fields(CoreConfig.fields_controls_eo, 'data')
def create_or_update_officer(user_id, data, audit_user_id, connected):
    officer_fields = {
        "username": "code",
        "other_names": "other_names",
        "last_name": "last_name",
        "phone": "phone",
        "email": "email",
        "birth_date": "dob",
        "address": "address",
        "works_to": "works_to",
        "location_id": "location_id",
        # TODO veo_code, last_name, other_names, dob, phone
        "substitution_officer_id": "substitution_officer_id",
        "phone_communication": "phone_communication",
    }
    data_subset = {v: data.get(k) for k, v in officer_fields.items()}
    data_subset["audit_user_id"] = audit_user_id
    data_subset["has_login"] = connected
    if user_id:
        # TODO we might want to update a user that has been deleted. Use Legacy ID ?
        officer = Officer.objects.filter(
            validity_to__isnull=True, user__id=user_id
        ).first()
        if officer is not None and officer.validity_to is not None:
            raise ValidationError(_('core.user.edit_historical_data_error'))
    else:
        officer = Officer.objects.filter(
            code=data_subset["code"], validity_to__isnull=True
        ).first()

    if officer:
        officer.save_history()
        [setattr(officer, k, v) for k, v in data_subset.items()]
        created = False
    else:
        officer = Officer(**data_subset)
        created = True

    officer.save()
    if data.get("village_ids"):
        create_or_update_officer_villages(
            officer, data["village_ids"], data_subset["audit_user_id"]
        )
    return officer, created


def create_or_update_claim_admin(user_id, data, audit_user_id, connected):
    ca_fields = {
        "username": "code",
        "other_names": "other_names",
        "last_name": "last_name",
        "phone": "phone",
        "email": "email_id",
        "birth_date": "dob",
        "health_facility_id": "health_facility_id",
    }
    data_subset = {v: data.get(k) for k, v in ca_fields.items()}
    data_subset["audit_user_id"] = audit_user_id
    data_subset["has_login"] = connected
    # Since ClaimAdmin is not in the core module, we have to dynamically load it.
    # If the Claim module is not loaded and someone requests a ClaimAdmin, this will raise an Exception
    claim_admin_class = apps.get_model("claim", "ClaimAdmin")
    if user_id:
        # TODO we might want to update a user that has been deleted. Use Legacy ID ?
        claim_admin = claim_admin_class.objects.filter(validity_to__isnull=True, user__id=user_id).first()
        if claim_admin is not None and claim_admin.validity_to is not None:
            raise ValidationError(_('core.user.edit_historical_data_error'))
    else:
        claim_admin = claim_admin_class.objects.filter(code=data_subset["code"], validity_to__isnull=True).first()

    if claim_admin:
        claim_admin.save_history()
        [setattr(claim_admin, k, v) for k, v in data_subset.items()]
        created = False
    else:
        claim_admin = claim_admin_class(**data_subset)
        created = True

    # TODO update municipalities, regions
    claim_admin.save()
    return claim_admin, created


def _business_object_pk(model_label, object_ref):
    """
    Resolve a business object reference (pk or uuid) to its primary key, or None when it
    does not designate an existing row.
    """
    from core.models.user_business_access import resolve_business_content_type

    content_type = resolve_business_content_type(model_label)
    model = content_type.model_class() if content_type else None
    if model is None:
        logger.warning("Unknown business object model '%s'", model_label)
        return None
    queryset = model.objects.all()
    if any(f.name == "uuid" for f in model._meta.fields):
        pk = queryset.filter(uuid=object_ref).values_list("pk", flat=True).first()
        if pk is not None:
            return pk
    try:
        return queryset.filter(pk=object_ref).values_list("pk", flat=True).first()
    except (ValidationError, ValueError, TypeError):
        return None


def create_or_update_user_business_accesses(core_user, business_accesses, audit_user=None):
    """
    Replace the whole set of UBA links of `core_user` with `business_accesses`, a list of
    `{"link_type", "business_object_model", "object_id"}`. Links left out are soft deleted
    so their history survives.

    `business_accesses` None means "leave the links alone", an empty list means "drop them
    all", the same convention the right bags use.
    """
    if business_accesses is None:
        return list(UserBusinessAccess.objects.filter(user=core_user, active=True))

    wanted = {}
    for entry in business_accesses:
        model_label = entry.get("business_object_model")
        link_type = (entry.get("link_type") or "").strip()
        if not is_valid_for(link_type, model_label):
            raise ValidationError(
                f"'{link_type}' is not a UBA link type registered for {model_label}")
        content_type = resolve_business_content_type(model_label)
        object_pk = _business_object_pk(model_label, entry.get("object_id"))
        if content_type is None or object_pk is None:
            raise ValidationError(
                f"No {model_label} matching '{entry.get('object_id')}'")
        wanted[(link_type, content_type.id, str(object_pk))] = None

    kept = []
    for business_access in UserBusinessAccess.objects.filter(user=core_user, active=True):
        key = (business_access.link_type, business_access.content_type_id, business_access.object_id)
        if key in wanted and wanted[key] is None:
            wanted[key] = business_access
            kept.append(business_access)
        else:
            # dropped by this call, or a duplicate of a link we already kept
            business_access.delete(user=audit_user)

    for (link_type, content_type_id, object_id), existing in wanted.items():
        if existing is not None:
            continue
        business_access = UserBusinessAccess(
            user=core_user,
            link_type=link_type,
            content_type_id=content_type_id,
            object_id=object_id,
        )
        business_access.save(user=audit_user)
        kept.append(business_access)
    return kept


def business_access_object_ids(core_user, link_type, model_label):
    """Primary keys of the business objects `core_user` is linked to under `link_type`."""
    content_type = resolve_business_content_type(model_label)
    if content_type is None:
        return []
    return [
        int(object_id) for object_id in UserBusinessAccess.objects.filter(
            user=core_user, link_type=link_type, content_type=content_type, active=True
        ).values_list("object_id", flat=True)
        if str(object_id).isdigit()
    ]


def align_claim_admin_with_business_access(core_user, audit_user_id=None):
    """
    Derive the claim admin entry from the CLAIM_ADMIN links, the dedicated ClaimAdmin table
    staying in place next to UBA.

    No link: nothing to derive, the entry is left untouched. No claim admin yet: create one
    on the first linked HF. A claim admin already there: keep its HF when it is one of the
    linked ones, otherwise move it to the first linked one.

    Never assigns a role. Attaching an HF must not pull the standard claim admin role onto
    the user, that coupling is what UBA exists to remove.
    """
    from core.apps import CLAIM_ADMIN_UBA_LINK_TYPE, HEALTH_FACILITY_MODEL

    hf_ids = business_access_object_ids(core_user, CLAIM_ADMIN_UBA_LINK_TYPE, HEALTH_FACILITY_MODEL)
    if not hf_ids:
        return None

    claim_admin = core_user.claim_admin
    if claim_admin is not None and claim_admin.health_facility_id in hf_ids:
        return claim_admin

    if claim_admin is None:
        claim_admin_class = apps.get_model("claim", "ClaimAdmin")
        i_user = core_user.i_user
        claim_admin = claim_admin_class(
            code=core_user.username,
            last_name=i_user.last_name if i_user else core_user.username,
            other_names=i_user.other_names if i_user else "",
            email_id=i_user.email if i_user else None,
            phone=i_user.phone if i_user else None,
            has_login=True,
            audit_user_id=audit_user_id,
        )
    else:
        claim_admin.save_history()
    claim_admin.health_facility_id = hf_ids[0]
    claim_admin.save()
    if core_user.claim_admin_id != claim_admin.id:
        core_user.claim_admin = claim_admin
        core_user.save()
    return claim_admin


def align_officer_villages_with_business_access(core_user, audit_user_id=None):
    """
    Derive the enrolment officer villages from the ENROLMENT links, the dedicated
    OfficerVillage table staying in place next to UBA.

    Mirrors `align_claim_admin_with_business_access`: no link leaves the officer untouched,
    otherwise the officer villages are aligned on the linked ones. Never assigns a role.
    """
    from core.apps import ENROLMENT_UBA_LINK_TYPE, VILLAGE_MODEL

    village_ids = business_access_object_ids(core_user, ENROLMENT_UBA_LINK_TYPE, VILLAGE_MODEL)
    if not village_ids:
        return None

    officer = core_user.officer
    if officer is None:
        i_user = core_user.i_user
        officer = Officer(
            code=core_user.username,
            last_name=i_user.last_name if i_user else core_user.username,
            other_names=i_user.other_names if i_user else "",
            email=i_user.email if i_user else None,
            phone=i_user.phone if i_user else None,
            location_id=village_ids[0],
            has_login=True,
            audit_user_id=audit_user_id,
        )
        officer.save()
        core_user.officer = officer
        core_user.save()
    elif officer.location_id not in village_ids:
        officer.save_history()
        officer.location_id = village_ids[0]
        officer.save()

    create_or_update_officer_villages(officer, village_ids, audit_user_id)
    return officer


def create_or_update_core_user(user_uuid, username, i_user=None, t_user=None, officer=None, claim_admin=None,
                               audit_user=None, audit_user_id=None, business_accesses=None):
    if user_uuid:
        # This intentionally fails if the provided uuid doesn't exist as we don't want clients to set it
        user = User.objects.get(id=user_uuid)
        # There is no history to save for User
        created = False
    elif username:
        user = User.objects.filter(username=username).first()
        created = False
    else:
        user = None
        created = False

    if not user:
        user = User(username=username)
        created = True
    if username:
        user.username = username
    if i_user:
        user.i_user = i_user
    if t_user:
        user.t_user = t_user
    if officer:
        user.officer = officer
    if claim_admin:
        user.claim_admin = claim_admin
    user.save()
    # UBA is the input: the links are stored first, then the dedicated claim admin and
    # enrolment officer entries are derived from them. Both tables keep working as before.
    create_or_update_user_business_accesses(user, business_accesses, audit_user=audit_user)
    align_claim_admin_with_business_access(user, audit_user_id=audit_user_id)
    align_officer_villages_with_business_access(user, audit_user_id=audit_user_id)
    return user, created


def change_user_password(logged_user, username_to_update=None, old_password=None, new_password=None):
    if username_to_update and username_to_update != logged_user.username:
        if not logged_user.has_perms(CoreConfig.gql_mutation_update_users_perms):
            raise PermissionDenied("unauthorized")
        user_to_update = User.objects.get(username=username_to_update)
    else:
        user_to_update = logged_user
        old_password_match = old_password and user_to_update.check_password(old_password)
        if not (old_password_match or user_to_update.stored_password == CoreConfig.locked_user_password_hash):
            raise ValidationError(_("core.wrong_old_password"))

    user_to_update.set_password(new_password)
    user_to_update.save()


def set_user_password(request, username, token, password):
    user = User.objects.get(username=username)

    if default_token_generator.check_token(user, token):
        user.set_password(password)
        user.save()
    else:
        raise ValidationError("Invalid Token")


def check_user_unique_email(user_email):
    if InteractiveUser.objects.filter(email=user_email, validity_to__isnull=True).exists():
        return [{"message": "User email %s already exists" % user_email}]
    return []


def reset_user_password(request, username):
    user = User.objects.get(username=username)
    user.clear_refresh_tokens()

    if not user.email:
        raise ValidationError(
            f"User {username} cannot reset password because he has no email address"
        )

    token = default_token_generator.make_token(user)
    try:
        logger.info(f"Send mail to reset password for {user} with token '{token}'")
        params = urlencode({"token": token})
        reset_url = f"{settings.FRONTEND_URL}/set_password?{params}"
        message = loader.render_to_string(
            CoreConfig.password_reset_template,
            {
                "reset_url": reset_url,
                "user": user,
            },
        )
        logger.debug("Message sent: %s" % message)
        email_to_send = send_mail(
            subject="[OpenIMIS] Reset Password",
            message=message,
            from_email=settings.EMAIL_HOST_USER,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return email_to_send
    except BadHeaderError:
        return ValueError("Invalid header found.")


def user_authentication(request, username, password):
    if not username or not password:
        raise exceptions.ParseError(_("Missing username or password"))
    user = authenticate(request, username=username, password=password)
    if not user:
        logger.debug(f"Authentication failed for username: {username}")
        raise exceptions.AuthenticationFailed("INCORRECT_CREDENTIALS")
    return user