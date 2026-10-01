from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from core import datetime, datetimedelta
from core.apps import CoreConfig
from core.models import RoleRight, UserRole
from core.schema import Query, update_or_create_role
from core.test_helpers import (
    create_test_interactive_user,
    create_test_officer,
    create_test_role,
    create_test_user_business_access,
)
from core.uba_link_types import register_uba_link_type, unregister_uba_link_type

# a right the role carries globally, and one it only carries through a UBA link
_GLOBAL_RIGHT = "101101"
_UBA_RIGHT = "111002"
_UNKNOWN_RIGHT = "111001"

# the credentials a user may act under on a business object, they are not openIMIS roles
_LINK_TYPE = "TEST_ACCOUNTANT"
_OTHER_LINK_TYPE = "TEST_CLAIM_ADMIN"
_UNREGISTERED_LINK_TYPE = "TEST_NOT_REGISTERED"


class UserBusinessAccessPermissionTest(TestCase):
    """Coverage of RoleRight.uba and the UBA branch of User.has_perms."""

    def setUp(self):
        cache.clear()
        register_uba_link_type(_LINK_TYPE, "Accountant", models=("core.officer",))
        register_uba_link_type(_OTHER_LINK_TYPE, "Claim admin", models=("core.officer",))
        self.officer = create_test_officer(valid=True, custom_props={"code": "UBAOFF1"})
        self.other_officer = create_test_officer(valid=True, custom_props={"code": "UBAOFF2"})
        self.role = create_test_role(
            name="UBA test role",
            rights=[int(_GLOBAL_RIGHT)],
            uba_rights=[int(_UBA_RIGHT)],
        )
        self.user = create_test_interactive_user(username="ubatestuser", roles=[self.role.id])
        self.business_map = ["core.officer", self.officer.uuid]

    def tearDown(self):
        cache.clear()
        unregister_uba_link_type(_LINK_TYPE)
        unregister_uba_link_type(_OTHER_LINK_TYPE)

    def _grant_business_access(self, business_object=None, link_type=_LINK_TYPE):
        return create_test_user_business_access(
            user=self.user,
            business_object=business_object or self.officer,
            link_type=link_type,
        )

    def test_global_right_granted_without_business_map(self):
        self.assertTrue(self.user.has_perms([_GLOBAL_RIGHT]))

    def test_uba_right_not_in_the_global_bag(self):
        self.assertFalse(self.user.has_perms([_UBA_RIGHT]))

    def test_uba_right_denied_without_business_access_row(self):
        self.assertFalse(self.user.has_perms([_UBA_RIGHT], access_requirements=self.business_map))

    def test_uba_right_granted_on_the_linked_instance(self):
        self._grant_business_access()
        self.assertTrue(self.user.has_perms([_UBA_RIGHT], access_requirements=self.business_map))

    def test_uba_right_denied_on_another_instance(self):
        self._grant_business_access()
        self.assertFalse(self.user.has_perms(
            [_UBA_RIGHT], access_requirements=["core.officer", self.other_officer.uuid]))

    def test_business_map_accepts_the_primary_key_too(self):
        self._grant_business_access()
        self.assertTrue(self.user.has_perms(
            [_UBA_RIGHT], access_requirements=["core.officer", self.officer.id]))

    def test_business_map_demanding_the_matching_link_type(self):
        self._grant_business_access(link_type=_LINK_TYPE)
        matching = ["core.officer", self.officer.uuid, _LINK_TYPE]
        other = ["core.officer", self.officer.uuid, _OTHER_LINK_TYPE]
        self.assertTrue(self.user.has_perms([_UBA_RIGHT], access_requirements=matching))
        self.assertFalse(self.user.has_perms([_UBA_RIGHT], access_requirements=other))

    def test_business_map_accepting_any_of_several_link_types(self):
        self._grant_business_access(link_type=_OTHER_LINK_TYPE)
        any_of = ["core.officer", self.officer.uuid, [_LINK_TYPE, _OTHER_LINK_TYPE]]
        self.assertTrue(self.user.has_perms([_UBA_RIGHT], access_requirements=any_of))

    def test_business_map_without_link_type_accepts_any_credential(self):
        self._grant_business_access(link_type=_OTHER_LINK_TYPE)
        self.assertTrue(self.user.has_perms([_UBA_RIGHT], access_requirements=self.business_map))

    def test_unregistered_link_type_in_the_map_grants_nothing(self):
        self._grant_business_access()
        self.assertFalse(self.user.has_perms(
            [_UBA_RIGHT],
            access_requirements=["core.officer", self.officer.uuid, _UNREGISTERED_LINK_TYPE]))

    def test_several_credentials_on_the_same_object(self):
        # the accountant and claim admin of one HF is two rows, not one multi valued row
        self._grant_business_access(link_type=_LINK_TYPE)
        self._grant_business_access(link_type=_OTHER_LINK_TYPE)
        for link_type in (_LINK_TYPE, _OTHER_LINK_TYPE):
            self.assertTrue(self.user.has_perms(
                [_UBA_RIGHT], access_requirements=["core.officer", self.officer.uuid, link_type]))

    def test_revoking_the_role_strips_the_right_even_though_the_link_stays(self):
        # the link no longer references a role, revocation is enforced through the bag
        self._grant_business_access()
        self.assertTrue(self.user.has_perms([_UBA_RIGHT], access_requirements=self.business_map))
        UserRole.objects.filter(user=self.user.i_user).update(validity_to=datetime.datetime.now())
        cache.clear()
        self.assertFalse(self.user.has_perms([_UBA_RIGHT], access_requirements=self.business_map))

    def test_several_business_maps(self):
        self._grant_business_access(self.other_officer)
        access_requirements = [
            ["core.officer", self.officer.uuid],
            ["core.officer", self.other_officer.uuid],
        ]
        self.assertTrue(self.user.has_perms([_UBA_RIGHT], access_requirements=access_requirements))

    def test_global_right_still_granted_with_a_business_map(self):
        self.assertTrue(self.user.has_perms([_GLOBAL_RIGHT], access_requirements=self.business_map))

    def test_global_right_granted_on_a_linked_instance_even_without_uba_row(self):
        # the global bag is checked first, no UserBusinessAccess row is needed
        self.assertTrue(self.user.has_perms(
            [_GLOBAL_RIGHT], access_requirements=["core.officer", self.other_officer.uuid]))

    def test_same_right_in_both_bags(self):
        RoleRight.objects.create(role=self.role, right_id=int(_UBA_RIGHT), uba=False, audit_user_id=-1)
        cache.clear()
        self._grant_business_access()
        self.assertTrue(self.user.has_perms([_UBA_RIGHT]))
        self.assertTrue(self.user.has_perms([_UBA_RIGHT], access_requirements=self.business_map))

    def test_right_absent_from_the_role_is_never_granted(self):
        self._grant_business_access()
        self.assertFalse(self.user.has_perms([_UNKNOWN_RIGHT], access_requirements=self.business_map))

    def test_all_perms_of_the_list_must_be_granted(self):
        self._grant_business_access()
        self.assertTrue(self.user.has_perms([_GLOBAL_RIGHT, _UBA_RIGHT], access_requirements=self.business_map))
        self.assertFalse(self.user.has_perms(
            [_GLOBAL_RIGHT, _UBA_RIGHT, _UNKNOWN_RIGHT], access_requirements=self.business_map))

    def test_expired_business_access_row_grants_nothing(self):
        business_access = self._grant_business_access()
        business_access.date_valid_to = datetime.datetime.now() - datetimedelta(days=1)
        business_access.save()
        self.assertFalse(self.user.has_perms([_UBA_RIGHT], access_requirements=self.business_map))

    def test_future_business_access_row_grants_nothing(self):
        business_access = self._grant_business_access()
        business_access.date_valid_from = datetime.datetime.now() + datetimedelta(days=1)
        business_access.save()
        self.assertFalse(self.user.has_perms([_UBA_RIGHT], access_requirements=self.business_map))

    def test_deactivated_business_access_row_grants_nothing(self):
        business_access = self._grant_business_access()
        business_access.delete()
        self.assertFalse(business_access.active)
        self.assertFalse(self.user.has_perms([_UBA_RIGHT], access_requirements=self.business_map))

    def test_unknown_model_label_is_ignored(self):
        self._grant_business_access()
        self.assertFalse(self.user.has_perms(
            [_UBA_RIGHT], access_requirements=["core.doesnotexist", self.officer.uuid]))
        self.assertFalse(self.user.has_perms([_UBA_RIGHT], access_requirements=["notalabel"]))

    def test_rights_bags(self):
        i_user = self.user.i_user
        self.assertIn(int(_GLOBAL_RIGHT), i_user.rights)
        self.assertNotIn(int(_UBA_RIGHT), i_user.rights)
        self.assertIn(int(_GLOBAL_RIGHT), i_user.rights_for_uba)
        self.assertIn(int(_UBA_RIGHT), i_user.rights_for_uba)
        self.assertNotIn(_UBA_RIGHT, i_user.rights_str)

    def test_uba_rights_bag_is_disjoint_from_the_global_one(self):
        i_user = self.user.i_user
        self.assertIn(int(_UBA_RIGHT), i_user.uba_rights)
        self.assertNotIn(int(_GLOBAL_RIGHT), i_user.uba_rights)
        self.assertFalse(set(i_user.rights) & set(i_user.uba_rights))

    def test_serializer_splits_the_two_bags(self):
        from core.serializers import UserSerializer

        data = UserSerializer(self.user).data["i_user"]
        self.assertIn(int(_GLOBAL_RIGHT), data["rights"])
        self.assertNotIn(int(_UBA_RIGHT), data["rights"])
        self.assertEqual([int(_UBA_RIGHT)], data["uba_rights"])

    def test_uba_rights_bag_is_disjoint_from_the_global_one(self):
        i_user = self.user.i_user
        self.assertIn(int(_UBA_RIGHT), i_user.uba_rights)
        self.assertNotIn(int(_GLOBAL_RIGHT), i_user.uba_rights)
        self.assertFalse(set(i_user.rights) & set(i_user.uba_rights))

    def test_serializer_splits_the_two_bags(self):
        from core.serializers import UserSerializer

        data = UserSerializer(self.user).data["i_user"]
        self.assertIn(int(_GLOBAL_RIGHT), data["rights"])
        self.assertNotIn(int(_UBA_RIGHT), data["rights"])
        self.assertEqual([int(_UBA_RIGHT)], data["uba_rights"])


class RoleRightUbaMutationTest(TestCase):
    """The two right bags survive a role create/update round trip."""

    def setUp(self):
        cache.clear()
        self.user = create_test_interactive_user(username="ubarolemut")

    def tearDown(self):
        cache.clear()

    @staticmethod
    def _bag(role, uba):
        return sorted(RoleRight.objects.filter(
            role_id=role.id, uba=uba, validity_to__isnull=True).values_list('right_id', flat=True))

    def test_create_role_with_both_bags(self):
        role = update_or_create_role({
            "name": "UBA mutation role",
            "is_system": 0,
            "is_blocked": False,
            "audit_user_id": -1,
            "validity_from": datetime.datetime.now(),
            "rights_id": [101101],
            "uba_rights_id": [111002],
        }, self.user)
        self.assertEqual(self._bag(role, False), [101101])
        self.assertEqual(self._bag(role, True), [111002])

    def test_rights_default_to_the_global_bag(self):
        # an older client that does not send uba_rights_id keeps creating global rights
        role = update_or_create_role({
            "name": "UBA legacy client role",
            "is_system": 0,
            "is_blocked": False,
            "audit_user_id": -1,
            "validity_from": datetime.datetime.now(),
            "rights_id": [101101],
        }, self.user)
        self.assertEqual(self._bag(role, False), [101101])
        self.assertEqual(self._bag(role, True), [])

    def test_update_role_moves_a_right_from_one_bag_to_the_other(self):
        role = create_test_role(name="UBA moving role", rights=[101101, 111002])
        update_or_create_role({
            "uuid": role.uuid,
            "name": role.name,
            "is_system": role.is_system,
            "is_blocked": role.is_blocked,
            "audit_user_id": -1,
            "rights_id": [101101],
            "uba_rights_id": [111002],
        }, self.user)
        self.assertEqual(self._bag(role, False), [101101])
        self.assertEqual(self._bag(role, True), [111002])

    def test_update_role_leaves_the_untouched_bag_alone(self):
        role = create_test_role(name="UBA partial role", rights=[101101], uba_rights=[111002])
        update_or_create_role({
            "uuid": role.uuid,
            "name": role.name,
            "is_system": role.is_system,
            "is_blocked": role.is_blocked,
            "audit_user_id": -1,
            "rights_id": [101102],
        }, self.user)
        self.assertEqual(self._bag(role, False), [101102])
        self.assertEqual(self._bag(role, True), [111002])

    def test_role_rights_cache_is_dropped_on_update(self):
        role = create_test_role(name="UBA cached role", rights=[101101])
        user = create_test_interactive_user(username="ubacacheduser", roles=[role.id])
        self.assertIn("101101", user.i_user.rights_str)
        update_or_create_role({
            "uuid": role.uuid,
            "name": role.name,
            "is_system": role.is_system,
            "is_blocked": role.is_blocked,
            "audit_user_id": -1,
            "rights_id": [],
            "uba_rights_id": [101101],
        }, self.user)
        self.assertNotIn("101101", user.i_user.rights_str)


class UserBusinessAccessQueryTest(TestCase):
    """
    The read side of the links: managing the links of other users is behind the query
    perms, reading one's own is not.
    """

    class _Info:
        """Enough of a ResolveInfo for resolvers that only look at the user."""

        def __init__(self, user):
            self.context = SimpleNamespace(user=user)

    def setUp(self):
        cache.clear()
        register_uba_link_type(_LINK_TYPE, "Accountant", models=("core.officer",))
        register_uba_link_type(_OTHER_LINK_TYPE, "Claim admin", models=("core.claimadmin",))
        self.officer = create_test_officer(valid=True, custom_props={"code": "UBAQRY1"})
        self.role = create_test_role(name="UBA query role", rights=[int(_GLOBAL_RIGHT)])
        self.user = create_test_interactive_user(username="ubaqueryuser", roles=[self.role.id])
        self.other_user = create_test_interactive_user(username="ubaqueryother", roles=[self.role.id])
        self.mine = create_test_user_business_access(self.user, self.officer, _LINK_TYPE)
        self.theirs = create_test_user_business_access(self.other_user, self.officer, _LINK_TYPE)

    def tearDown(self):
        unregister_uba_link_type(_LINK_TYPE)
        unregister_uba_link_type(_OTHER_LINK_TYPE)

    def _resolve_links(self, user):
        # the optimizer needs a real ResolveInfo, the branch under test does not
        with patch("core.schema.gql_optimizer.query", side_effect=lambda queryset, info: queryset):
            return Query.resolve_user_business_access(None, self._Info(user))

    def _resolve_link_types(self, user, business_object_model=None):
        return Query.resolve_uba_link_types(None, self._Info(user), business_object_model=business_object_model)

    def test_own_links_are_readable_without_the_query_perms(self):
        self.assertEqual(list(self._resolve_links(self.user)), [self.mine])

    def test_links_of_others_need_the_query_perms(self):
        self.assertNotIn(self.theirs, list(self._resolve_links(self.user)))

    def test_every_link_is_readable_with_the_query_perms(self):
        with patch.object(CoreConfig, "gql_query_user_business_access_perms", [_GLOBAL_RIGHT]):
            links = list(self._resolve_links(self.user))
        self.assertIn(self.mine, links)
        self.assertIn(self.theirs, links)

    def test_anonymous_reads_nothing(self):
        with self.assertRaises(PermissionDenied):
            self._resolve_links(AnonymousUser())

    def test_the_self_view_only_carries_the_links_that_count(self):
        self.mine.active = False
        self.mine.save(user=self.user)
        self.assertEqual(list(self._resolve_links(self.user)), [])

    def test_the_self_view_leaves_out_an_expired_link(self):
        self.mine.date_valid_to = datetime.datetime.now() - datetimedelta(days=1)
        self.mine.save(user=self.user)
        self.assertEqual(list(self._resolve_links(self.user)), [])

    def test_the_registry_is_readable_by_any_user(self):
        codes = [link_type.code for link_type in self._resolve_link_types(self.user)]
        self.assertIn(_LINK_TYPE, codes)
        self.assertIn(_OTHER_LINK_TYPE, codes)

    def test_the_registry_can_be_restricted_to_one_model(self):
        codes = [link_type.code for link_type in self._resolve_link_types(self.user, "core.officer")]
        self.assertIn(_LINK_TYPE, codes)
        self.assertNotIn(_OTHER_LINK_TYPE, codes)

    def test_the_registry_is_not_readable_anonymously(self):
        with self.assertRaises(PermissionDenied):
            self._resolve_link_types(AnonymousUser())
