"""
Guards on the permission declarations in `core.apps`.

The rights are enforced elsewhere; what these protect is the declaration layer, where
the failures are silent:

  * `has_perms([])` returns True by design, so a right list that resolves to empty
    grants the action to everyone rather than denying it.
  * `__load_config` only assigns config keys that already exist as attributes on
    CoreConfig, so a key declared in DEFAULT_CFG without one is never loaded and
    reading it raises AttributeError - which is how a right ends up unenforceable.
  * The numeric right is what is stored on a role, so changing one revokes access for
    every role already granted it. The expected ids are pinned below.

Note on UBA: `activate` / `deactivate` deliberately share the `update` right id rather
than claiming two more catalog entries - only their django names differ. That sharing is
asserted here on purpose, so splitting them later has to be a deliberate change.
"""

from django.test import TestCase

from core.apps import (
    DEFAULT_CFG,
    DJANGO_PERMS,
    CoreConfig,
    _PERM_CFG,
    django_perms,
    perms,
)

# The right ids as deployed. Changing one is a breaking change for existing roles.
EXPECTED_RIGHTS = {
    "gql_query_users_perms": ["121701"],
    "gql_mutation_create_users_perms": ["121702"],
    "gql_mutation_update_users_perms": ["121703"],
    "gql_mutation_delete_users_perms": ["121704"],
    "gql_query_user_business_access_perms": ["122501"],
    "gql_mutation_create_user_business_access_perms": ["122502"],
    "gql_mutation_update_user_business_access_perms": ["122503"],
    "gql_mutation_delete_user_business_access_perms": ["122504"],
    "gql_mutation_activate_user_business_access_perms": ["122503"],
    "gql_mutation_deactivate_user_business_access_perms": ["122503"],
    "gql_query_roles_perms": ["122001"],
    "gql_mutation_create_roles_perms": ["122002"],
    "gql_mutation_update_roles_perms": ["122003"],
    "gql_mutation_replace_roles_perms": ["122006"],
    "gql_mutation_duplicate_roles_perms": ["122005"],
    "gql_mutation_delete_roles_perms": ["122004"],
    "gql_query_enrolment_officers_perms": ["121501"],
    "gql_mutation_create_enrolment_officers_perms": ["121502"],
    "gql_mutation_update_enrolment_officers_perms": ["121503"],
    "gql_mutation_delete_enrolment_officers_perms": ["121504"],
    "gql_query_claim_administrator_perms": ["121601"],
    "gql_mutation_create_claim_administrator_perms": ["121602"],
    "gql_mutation_update_claim_administrator_perms": ["121603"],
    "gql_mutation_delete_claim_administrator_perms": ["121604"],
}

# Actions that share another action's right id, on purpose.
INTENTIONALLY_SHARED = {
    ("userBusinessAccess", "activate"): ("userBusinessAccess", "update"),
    ("userBusinessAccess", "deactivate"): ("userBusinessAccess", "update"),
}


class PermissionDeclarationTestCase(TestCase):
    def test_right_ids_unchanged(self):
        self.assertEqual(
            {key: DEFAULT_CFG[key] for key in EXPECTED_RIGHTS}, EXPECTED_RIGHTS
        )

    def test_perm_cfg_covers_every_declared_right(self):
        declared = {
            (entity, action)
            for entity, actions in DJANGO_PERMS.items()
            for action in actions
        }
        self.assertEqual(set(_PERM_CFG.values()), declared)

    def test_perm_cfg_matches_config_attributes(self):
        """
        `__load_config` skips config keys with no attribute here, so a missing one
        means the right is declared but never enforceable.
        """
        missing = [key for key in _PERM_CFG if not hasattr(CoreConfig, key)]
        self.assertEqual(missing, [])

    def test_no_right_list_is_empty(self):
        empty = [key for key in _PERM_CFG if not DEFAULT_CFG[key]]
        self.assertEqual(empty, [], f"empty right lists grant access to all: {empty}")

    def test_configured_attributes_are_populated(self):
        for key in _PERM_CFG:
            with self.subTest(key=key):
                self.assertEqual(getattr(CoreConfig, key), DEFAULT_CFG[key])

    def test_shared_right_ids_are_only_the_intended_ones(self):
        seen = {}
        for entity, actions in DJANGO_PERMS.items():
            for action, (_, right_id) in actions.items():
                seen.setdefault(right_id, []).append((entity, action))
        for right_id, holders in seen.items():
            if len(holders) == 1:
                continue
            for holder in holders:
                with self.subTest(right=right_id, holder=holder):
                    target = INTENTIONALLY_SHARED.get(holder)
                    self.assertTrue(
                        target is None or target in holders,
                        f"{holder} shares right {right_id} unintentionally",
                    )

    def test_django_permission_names_are_unique(self):
        """Even the actions that share a right id keep distinct django names."""
        seen = {}
        for entity, actions in DJANGO_PERMS.items():
            for action, (name, _) in actions.items():
                seen.setdefault(name, []).append(f"{entity}.{action}")
        shared = {name: who for name, who in seen.items() if len(who) > 1}
        self.assertEqual(shared, {})

    def test_unknown_entity_or_action_raises(self):
        with self.assertRaises(KeyError):
            perms("nosuchentity", "query")
        with self.assertRaises(KeyError):
            perms("user", "nosuchaction")
        with self.assertRaises(KeyError):
            django_perms("user", "nosuchaction")

    def test_multi_action_returns_every_right(self):
        """`has_perms` ORs the list, so this is the "any of" form."""
        self.assertEqual(perms("role", "update", "replace"), ["122003", "122006"])
