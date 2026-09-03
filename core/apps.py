import sys
import os
import importlib
import logging
from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)

MODULE_NAME = "core"

# The codes of the two credentials generalizing the dedicated "claim admin of that HF"
# and "enrolment officer of those villages" associations. Both keep running in parallel
# with their dedicated table, UBA is now the input and the tables are derived from it.
#
# The codes live here so that a module can name a credential without depending on
# location, but the registration itself belongs to `location.apps`: the health facility
# and the village are its models, and it is the one that knows where they sit in the
# location tree - what the row filter needs to turn a credential into a queryset filter.
CLAIM_ADMIN_UBA_LINK_TYPE = "CLAIM_ADMIN"
ENROLMENT_UBA_LINK_TYPE = "ENROLMENT"

HEALTH_FACILITY_MODEL = "location.healthfacility"
VILLAGE_MODEL = "location.location"
# the loc_type an ENROLMENT link is held on, a village
VILLAGE_LOCATION_TYPE = "V"

# Permissions, keyed by entity then action. Each action carries the pair
# `(django permission name, openIMIS numeric right)`.
#
# Two names for one action because `RoleRight.right_id` is an IntegerField and
# `InteractiveUser.rights_str` compares against `str(int)`: a right can only ever be the
# decimal string of an integer, a django style name can never be stored on a role. So the
# **integer is what is enforced today** and the django name is declared next to it, ready
# for the day permissions move to django's own tables.
#
# `query` / `create` / `update` / `delete` line up with the default model permissions django
# creates at post_migrate. Anything else is a **business action** and only becomes a
# grantable django row once the model declares it in `Meta.permissions` — today only
# UserBusinessAccess does, the role actions below still need theirs before a switch.
DJANGO_PERMS = {
    # UBA owns the 1225xx block: 1217xx is the user, 1218xx the payer, 1219xx location and
    # 1221xx medical, so 1225xx was the first free one. TODO confirm the block with the
    # openIMIS right catalog before this leaves the PR.
    # activate / deactivate deliberately share the update right rather than claiming two
    # more catalog ids; only their django names are distinct.
    "userBusinessAccess": {
        "query": ("core.view_userbusinessaccess", 122501),
        "create": ("core.add_userbusinessaccess", 122502),
        "update": ("core.change_userbusinessaccess", 122503),
        "delete": ("core.delete_userbusinessaccess", 122504),
        "activate": ("core.activate_userbusinessaccess", 122503),
        "deactivate": ("core.deactivate_userbusinessaccess", 122503),
    },
    "user": {
        "query": ("core.view_user", 121701),
        "create": ("core.add_user", 121702),
        "update": ("core.change_user", 121703),
        "delete": ("core.delete_user", 121704),
    },
    "role": {
        "query": ("core.view_role", 122001),
        "create": ("core.add_role", 122002),
        "update": ("core.change_role", 122003),
        "delete": ("core.delete_role", 122004),
        "duplicate": ("core.duplicate_role", 122005),
        "replace": ("core.replace_role", 122006),
    },
    "enrolmentOfficer": {
        "query": ("core.view_officer", 121501),
        "create": ("core.add_officer", 121502),
        "update": ("core.change_officer", 121503),
        "delete": ("core.delete_officer", 121504),
    },
    "claimAdministrator": {
        "query": ("claim.view_claimadmin", 121601),
        "create": ("claim.add_claimadmin", 121602),
        "update": ("claim.change_claimadmin", 121603),
        "delete": ("claim.delete_claimadmin", 121604),
    },
}


def _perm_entries(entity, actions):
    try:
        entity_perms = DJANGO_PERMS[entity]
    except KeyError:
        raise KeyError(f"No permissions declared for '{entity}'")
    missing = [action for action in actions if action not in entity_perms]
    if missing:
        raise KeyError(f"No permission declared for {entity}.{missing}")
    return [entity_perms[action] for action in actions]


def perms(entity, *actions):
    """
    The openIMIS rights of those actions, as the list of decimal strings the `_perms`
    config and `has_perms` expect. This is what is enforced today.
    """
    return [str(right_id) for _, right_id in _perm_entries(entity, actions)]


def django_perms(entity, *actions):
    """
    The django permission names of those actions. Declared but **not** enforced yet: nothing
    grants them, so checking one today would deny every non superuser. Kept so the move to
    django permissions is a config change rather than an API one.
    """
    return [perm for perm, _ in _perm_entries(entity, actions)]


this = sys.modules[MODULE_NAME]

DEFAULT_CFG = {
    "username_code_length": "8",  # cannot be bigger than 50 unless modified length limit
    "username_changeable": True,
    "auto_provisioning_user_group": "user",
    "calendar_package": "core",
    "calendar_module": ".calendars.ad_calendar",
    "datetime_package": "core",
    "datetime_module": ".datetimes.ad_datetime",
    "shortstrfdate": "%d/%m/%Y",
    "longstrfdate": "%a %d %B %Y",
    "iso_raw_date": "False",
    "age_of_majority": "18",
    "async_mutations": "True" if os.environ.get("ASYNC", os.environ.get("MODE", "PROD")).lower() == "prod" else "False",
    "password_reset_template": "password_reset.txt",
    "currency": "$",
    "gql_query_users_perms": perms("user", "query"),
    "gql_mutation_create_users_perms": perms("user", "create"),
    "gql_mutation_update_users_perms": perms("user", "update"),
    "gql_mutation_delete_users_perms": perms("user", "delete"),
    "gql_query_user_business_access_perms": perms("userBusinessAccess", "query"),
    "gql_mutation_create_user_business_access_perms": perms("userBusinessAccess", "create"),
    "gql_mutation_update_user_business_access_perms": perms("userBusinessAccess", "update"),
    "gql_mutation_delete_user_business_access_perms": perms("userBusinessAccess", "delete"),
    "gql_mutation_activate_user_business_access_perms": perms("userBusinessAccess", "activate"),
    "gql_mutation_deactivate_user_business_access_perms": perms("userBusinessAccess", "deactivate"),
    "gql_query_roles_perms": perms("role", "query"),
    "gql_mutation_create_roles_perms": perms("role", "create"),
    "gql_mutation_update_roles_perms": perms("role", "update"),
    "gql_mutation_replace_roles_perms": perms("role", "replace"),
    "gql_mutation_duplicate_roles_perms": perms("role", "duplicate"),
    "gql_mutation_delete_roles_perms": perms("role", "delete"),
    # TODO consider moving that roles related to ClaimAdmin and EnrolmentOfficer
    #  into modules related to that type of user for example
    #  EnrolmentOfficer -> policy module, ClaimAdmin -> claim module etc
    "gql_query_enrolment_officers_perms": perms("enrolmentOfficer", "query"),
    "gql_mutation_create_enrolment_officers_perms": perms("enrolmentOfficer", "create"),
    "gql_mutation_update_enrolment_officers_perms": perms("enrolmentOfficer", "update"),
    "gql_mutation_delete_enrolment_officers_perms": perms("enrolmentOfficer", "delete"),
    "gql_query_claim_administrator_perms": perms("claimAdministrator", "query"),
    "gql_mutation_create_claim_administrator_perms": perms("claimAdministrator", "create"),
    "gql_mutation_update_claim_administrator_perms": perms("claimAdministrator", "update"),
    "gql_mutation_delete_claim_administrator_perms": perms("claimAdministrator", "delete"),
    "fields_controls_user": {},
    "fields_controls_eo": {},
    "is_valid_health_facility_contract_required": False,
    "secondary_calendar": None,
    "locked_user_password_hash": 'locked'
}


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.AutoField'  # Django 3.1+
    name = MODULE_NAME
    username_code_length = 8
    username_changeable = True
    age_of_majority = 18
    password_reset_template = "password_reset.txt"
    gql_query_user_business_access_perms = []
    gql_mutation_create_user_business_access_perms = []
    gql_mutation_update_user_business_access_perms = []
    gql_mutation_delete_user_business_access_perms = []
    gql_mutation_activate_user_business_access_perms = []
    gql_mutation_deactivate_user_business_access_perms = []
    gql_query_roles_perms = []
    gql_mutation_create_roles_perms = []
    gql_mutation_update_roles_perms = []
    gql_mutation_replace_roles_perms = []
    gql_mutation_duplicate_roles_perms = []
    gql_mutation_delete_roles_perms = []
    gql_query_users_perms = []
    gql_mutation_create_users_perms = []
    gql_mutation_update_users_perms = []
    gql_mutation_delete_users_perms = []
    # TODO consider moving that roles related to ClaimAdmin and EnrolmentOfficer
    #  into modules related to that type of user for example
    #  EnrolmentOfficer -> policy module, ClaimAdmin -> claim module etc
    gql_query_enrolment_officers_perms = []
    gql_mutation_create_enrolment_officers_perms = []
    gql_mutation_update_enrolment_officers_perms = []
    gql_mutation_delete_enrolment_officers_perms = []
    gql_query_claim_administrator_perms = []
    gql_mutation_create_claim_administrator_perms = []
    gql_mutation_update_claim_administrator_perms = []
    gql_mutation_delete_claim_administrator_perms = []
    is_valid_health_facility_contract_required = None
    locked_user_password_hash = None

    fields_controls_user = {}
    fields_controls_eo = {}
    secondary_calendar = None

    def _import_module(self, cfg, k):
        logger.info('import %s.%s' %
                    (cfg["%s_module" % k], cfg["%s_package" % k]))
        return importlib.import_module(
            cfg["%s_module" % k], package=cfg["%s_package" % k])

    def _configure_calendar(self, cfg):
        this.shortstrfdate = cfg["shortstrfdate"]
        this.longstrfdate = cfg["longstrfdate"]
        this.iso_raw_date = False if cfg["iso_raw_date"] is None else cfg["iso_raw_date"].lower(
        ) == "true"
        try:
            this.calendar = self._import_module(cfg, "calendar")
            this.datetime = self._import_module(cfg, "datetime")
        except Exception:
            logger.error('Failed to configure calendar, using default!\n%s: %s' % (
                sys.exc_info()[0].__name__, sys.exc_info()[1]))
            this.calendar = self._import_module(DEFAULT_CFG, "calendar")
            this.datetime = self._import_module(DEFAULT_CFG, "datetime")

    def _configure_user_config(self, cfg):
        this.username_code_length = int(cfg["username_code_length"])
        # Quick fix, this config has to be rebuilt
        CoreConfig.username_code_length = int(cfg["username_code_length"])
        CoreConfig.username_changeable = cfg["username_changeable"]

    def _configure_majority(self, cfg):
        this.age_of_majority = int(cfg["age_of_majority"])

    def _configure_currency(self, cfg):
        this.currency = str(cfg["currency"])

    def _configure_auto_provisioning(self, cfg):
        if bool(os.environ.get('NO_DATABASE', False)):
            logger.info('env NO_DATABASE set to True: no user auto provisioning possible!')
            return
        group = cfg["auto_provisioning_user_group"]
        this.auto_provisioning_user_group = group
        try:
            from .models import Group
            Group.objects.get(name=group)
        except Group.DoesNotExist:
            g = Group(name=group)
            g.save()
        try:
            from django.contrib.auth.models import Permission
            p = Permission.objects.get(codename="view_user")
            g.permissions.add(p)
            g.save()
        except Exception as e:
            logger.warning('Failed set auto_provisioning_user_group ' + str(e))

    def _configure_graphql(self, cfg):
        this.async_mutations = True if cfg["async_mutations"] is None else cfg["async_mutations"].lower() == "true"

    def _configure_permissions(self, cfg):
        CoreConfig.gql_query_user_business_access_perms = cfg["gql_query_user_business_access_perms"]
        CoreConfig.gql_mutation_create_user_business_access_perms = \
            cfg["gql_mutation_create_user_business_access_perms"]
        CoreConfig.gql_mutation_update_user_business_access_perms = \
            cfg["gql_mutation_update_user_business_access_perms"]
        CoreConfig.gql_mutation_delete_user_business_access_perms = \
            cfg["gql_mutation_delete_user_business_access_perms"]
        CoreConfig.gql_mutation_activate_user_business_access_perms = \
            cfg["gql_mutation_activate_user_business_access_perms"]
        CoreConfig.gql_mutation_deactivate_user_business_access_perms = \
            cfg["gql_mutation_deactivate_user_business_access_perms"]
        CoreConfig.gql_query_roles_perms = cfg["gql_query_roles_perms"]
        CoreConfig.gql_mutation_create_roles_perms = cfg["gql_mutation_create_roles_perms"]
        CoreConfig.gql_mutation_update_roles_perms = cfg["gql_mutation_update_roles_perms"]
        CoreConfig.gql_mutation_replace_roles_perms = cfg["gql_mutation_replace_roles_perms"]
        CoreConfig.gql_mutation_duplicate_roles_perms = cfg["gql_mutation_duplicate_roles_perms"]
        CoreConfig.gql_mutation_delete_roles_perms = cfg["gql_mutation_delete_roles_perms"]
        CoreConfig.gql_query_users_perms = cfg["gql_query_users_perms"]
        CoreConfig.gql_mutation_create_users_perms = cfg["gql_mutation_create_users_perms"]
        CoreConfig.gql_mutation_update_users_perms = cfg["gql_mutation_update_users_perms"]
        CoreConfig.gql_mutation_delete_users_perms = cfg["gql_mutation_delete_users_perms"]
        CoreConfig.gql_query_enrolment_officers_perms = cfg["gql_query_enrolment_officers_perms"]
        CoreConfig.gql_mutation_create_enrolment_officers_perms = cfg["gql_mutation_create_enrolment_officers_perms"]
        CoreConfig.gql_mutation_update_enrolment_officers_perms = cfg["gql_mutation_update_enrolment_officers_perms"]
        CoreConfig.gql_mutation_delete_enrolment_officers_perms = cfg["gql_mutation_delete_enrolment_officers_perms"]
        CoreConfig.gql_query_claim_administrator_perms = cfg["gql_query_claim_administrator_perms"]
        CoreConfig.gql_mutation_create_claim_administrator_perms = cfg["gql_mutation_create_claim_administrator_perms"]
        CoreConfig.gql_mutation_update_claim_administrator_perms = cfg["gql_mutation_update_claim_administrator_perms"]
        CoreConfig.gql_mutation_delete_claim_administrator_perms = cfg["gql_mutation_delete_claim_administrator_perms"]
        CoreConfig.gql_mutation_delete_claim_administrator_perms = cfg["gql_mutation_delete_claim_administrator_perms"]

        CoreConfig.fields_controls_user = cfg["fields_controls_user"]
        CoreConfig.fields_controls_eo = cfg["fields_controls_eo"]

    def _configure_additional_settings(self, cfg):
        CoreConfig.is_valid_health_facility_contract_required = cfg["is_valid_health_facility_contract_required"]
        CoreConfig.secondary_calendar = cfg["secondary_calendar"]

    def ready(self):
        from .models import ModuleConfiguration
        cfg = ModuleConfiguration.get_or_default(MODULE_NAME, DEFAULT_CFG)
        self._configure_calendar(cfg)
        self._configure_user_config(cfg)
        self._configure_majority(cfg)
        self._configure_auto_provisioning(cfg)
        self._configure_graphql(cfg)
        self._configure_currency(cfg)
        self._configure_permissions(cfg)
        self._configure_additional_settings(cfg)

        CoreConfig.password_reset_template = cfg["password_reset_template"]
        CoreConfig.locked_user_password_hash = cfg["locked_user_password_hash"]

        # The two credentials above are declared by `location.apps`, the module that owns
        # the health facility and the village they are held on: only it knows how they sit
        # in the location tree, which is what the row filter needs. Core keeps the codes so
        # that a module can name a credential without depending on location.

        # Connects the post_save/post_delete receivers invalidating the rights caches.
        # TODO: remove this import once the assembly is upgraded past 24.04. Newer
        # openimis-be_py ships a `receiver_binding` app (installed after
        # `signal_binding`, so that service signals are all bound before any receiver
        # loads) which imports `<module>.receivers` itself, making this import a
        # duplicate registration risk. The receivers use an explicit `dispatch_uid`,
        # so a double load is currently idempotent.
        from core import receivers  # noqa: F401

        # The scheduler starts as soon as it gets a job, which could be before Django is ready, so we enable it here
        from core import scheduler
        if settings.SCHEDULER_AUTOSTART:
            scheduler.start()
