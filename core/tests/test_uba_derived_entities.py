from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.apps import (
    CLAIM_ADMIN_UBA_LINK_TYPE,
    ENROLMENT_UBA_LINK_TYPE,
    HEALTH_FACILITY_MODEL,
    VILLAGE_MODEL,
)
from core.models import UserBusinessAccess, UserRole
from core.services.userServices import (
    align_claim_admin_with_business_access,
    align_officer_villages_with_business_access,
    create_or_update_user_business_accesses,
)
from core.test_helpers import create_test_interactive_user
from location.models import HealthFacility, Location, OfficerVillage
from location.test_helpers import create_test_health_facility, create_test_village


def _link(link_type, model_label, object_id):
    return {
        "link_type": link_type,
        "business_object_model": model_label,
        "object_id": str(object_id),
    }


class BusinessAccessInputTest(TestCase):
    """UBA is what the client sends; the set is replaced wholesale."""

    @classmethod
    def setUpTestData(cls):
        cls.village = create_test_village(custom_props={"name": "UbaInVillage"})
        cls.hf = create_test_health_facility("UBAIN1", cls.village.parent.parent_id)
        cls.other_hf = create_test_health_facility("UBAIN2", cls.village.parent.parent_id)

    def setUp(self):
        self.user = create_test_interactive_user(username="ubainuser")

    def _active(self, link_type=CLAIM_ADMIN_UBA_LINK_TYPE):
        return sorted(UserBusinessAccess.objects.filter(
            user=self.user, link_type=link_type, active=True).values_list("object_id", flat=True))

    def test_links_are_created(self):
        create_or_update_user_business_accesses(
            self.user, [_link(CLAIM_ADMIN_UBA_LINK_TYPE, HEALTH_FACILITY_MODEL, self.hf.pk)])

        self.assertEqual([str(self.hf.pk)], self._active())

    def test_none_leaves_the_links_alone(self):
        create_or_update_user_business_accesses(
            self.user, [_link(CLAIM_ADMIN_UBA_LINK_TYPE, HEALTH_FACILITY_MODEL, self.hf.pk)])

        create_or_update_user_business_accesses(self.user, None)

        self.assertEqual([str(self.hf.pk)], self._active())

    def test_empty_list_drops_every_link(self):
        create_or_update_user_business_accesses(
            self.user, [_link(CLAIM_ADMIN_UBA_LINK_TYPE, HEALTH_FACILITY_MODEL, self.hf.pk)])

        create_or_update_user_business_accesses(self.user, [])

        self.assertEqual([], self._active())

    def test_the_set_is_replaced_not_merged(self):
        create_or_update_user_business_accesses(
            self.user, [_link(CLAIM_ADMIN_UBA_LINK_TYPE, HEALTH_FACILITY_MODEL, self.hf.pk)])

        create_or_update_user_business_accesses(
            self.user, [_link(CLAIM_ADMIN_UBA_LINK_TYPE, HEALTH_FACILITY_MODEL, self.other_hf.pk)])

        self.assertEqual([str(self.other_hf.pk)], self._active())

    def test_a_uuid_reference_is_accepted(self):
        create_or_update_user_business_accesses(
            self.user, [_link(CLAIM_ADMIN_UBA_LINK_TYPE, HEALTH_FACILITY_MODEL, self.hf.uuid)])

        self.assertEqual([str(self.hf.pk)], self._active())

    def test_unregistered_link_type_is_refused(self):
        with self.assertRaises(ValidationError):
            create_or_update_user_business_accesses(
                self.user, [_link("NOT_REGISTERED", HEALTH_FACILITY_MODEL, self.hf.pk)])

    def test_link_type_not_declared_for_that_model_is_refused(self):
        with self.assertRaises(ValidationError):
            create_or_update_user_business_accesses(
                self.user, [_link(CLAIM_ADMIN_UBA_LINK_TYPE, VILLAGE_MODEL, self.village.pk)])

    def test_unknown_business_object_is_refused(self):
        with self.assertRaises(ValidationError):
            create_or_update_user_business_accesses(
                self.user, [_link(CLAIM_ADMIN_UBA_LINK_TYPE, HEALTH_FACILITY_MODEL, 999999999)])


class ClaimAdminDerivedFromBusinessAccessTest(TestCase):
    """The ClaimAdmin entry is derived from the CLAIM_ADMIN links, and kept in parallel."""

    @classmethod
    def setUpTestData(cls):
        cls.village = create_test_village(custom_props={"name": "UbaCaVillage"})
        cls.hf = create_test_health_facility("UBACA1", cls.village.parent.parent_id)
        cls.other_hf = create_test_health_facility("UBACA2", cls.village.parent.parent_id)

    def setUp(self):
        self.user = create_test_interactive_user(username="ubacauser")

    def _grant(self, *health_facilities):
        create_or_update_user_business_accesses(
            self.user,
            [_link(CLAIM_ADMIN_UBA_LINK_TYPE, HEALTH_FACILITY_MODEL, hf.pk) for hf in health_facilities])

    def test_no_link_leaves_the_claim_admin_alone(self):
        self.assertIsNone(align_claim_admin_with_business_access(self.user))
        self.assertIsNone(self.user.claim_admin)

    def test_claim_admin_created_on_the_first_linked_hf(self):
        self._grant(self.hf)

        claim_admin = align_claim_admin_with_business_access(self.user, audit_user_id=-1)

        self.assertIsNotNone(claim_admin)
        self.assertEqual(self.hf.pk, claim_admin.health_facility_id)
        self.user.refresh_from_db()
        self.assertEqual(claim_admin.id, self.user.claim_admin_id)

    def test_existing_hf_in_the_linked_list_is_kept(self):
        self._grant(self.hf, self.other_hf)
        first = align_claim_admin_with_business_access(self.user, audit_user_id=-1)
        self.user.refresh_from_db()

        again = align_claim_admin_with_business_access(self.user, audit_user_id=-1)

        self.assertEqual(first.health_facility_id, again.health_facility_id)

    def test_hf_moved_when_it_is_not_in_the_linked_list(self):
        self._grant(self.hf)
        align_claim_admin_with_business_access(self.user, audit_user_id=-1)
        self.user.refresh_from_db()

        self._grant(self.other_hf)
        claim_admin = align_claim_admin_with_business_access(self.user, audit_user_id=-1)

        self.assertEqual(self.other_hf.pk, claim_admin.health_facility_id)

    def test_deriving_never_assigns_a_role(self):
        before = set(UserRole.objects.filter(
            user=self.user.i_user, validity_to__isnull=True).values_list("role_id", flat=True))
        self._grant(self.hf)

        align_claim_admin_with_business_access(self.user, audit_user_id=-1)

        after = set(UserRole.objects.filter(
            user=self.user.i_user, validity_to__isnull=True).values_list("role_id", flat=True))
        self.assertEqual(before, after)


class OfficerVillagesDerivedFromBusinessAccessTest(TestCase):
    """The enrolment officer villages are derived from the ENROLMENT links."""

    @classmethod
    def setUpTestData(cls):
        cls.village = create_test_village(custom_props={"name": "UbaEoVillage"})
        cls.other_village = create_test_village(custom_props={"name": "UbaEoVillage2"})

    def setUp(self):
        self.user = create_test_interactive_user(username="ubaeouser")

    def _grant(self, *villages):
        create_or_update_user_business_accesses(
            self.user,
            [_link(ENROLMENT_UBA_LINK_TYPE, VILLAGE_MODEL, village.pk) for village in villages])

    def _officer_villages(self):
        return sorted(OfficerVillage.objects.filter(
            officer=self.user.officer, validity_to__isnull=True).values_list("location_id", flat=True))

    def test_no_link_leaves_the_officer_alone(self):
        self.assertIsNone(align_officer_villages_with_business_access(self.user))
        self.assertIsNone(self.user.officer)

    def test_officer_and_villages_created_from_the_links(self):
        self._grant(self.village, self.other_village)

        officer = align_officer_villages_with_business_access(self.user, audit_user_id=-1)

        self.assertIsNotNone(officer)
        self.user.refresh_from_db()
        self.assertEqual(sorted([self.village.pk, self.other_village.pk]), self._officer_villages())

    def test_villages_follow_the_links(self):
        self._grant(self.village, self.other_village)
        align_officer_villages_with_business_access(self.user, audit_user_id=-1)
        self.user.refresh_from_db()

        self._grant(self.other_village)
        align_officer_villages_with_business_access(self.user, audit_user_id=-1)

        self.assertEqual([self.other_village.pk], self._officer_villages())

    def test_deriving_never_assigns_a_role(self):
        before = set(UserRole.objects.filter(
            user=self.user.i_user, validity_to__isnull=True).values_list("role_id", flat=True))
        self._grant(self.village)

        align_officer_villages_with_business_access(self.user, audit_user_id=-1)

        after = set(UserRole.objects.filter(
            user=self.user.i_user, validity_to__isnull=True).values_list("role_id", flat=True))
        self.assertEqual(before, after)


class UbaLinkTypeRegistryTest(TestCase):
    def test_core_link_types_are_registered_for_their_models(self):
        from core.uba_link_types import get_uba_link_types, is_valid_for

        self.assertTrue(is_valid_for(CLAIM_ADMIN_UBA_LINK_TYPE, HEALTH_FACILITY_MODEL))
        self.assertTrue(is_valid_for(ENROLMENT_UBA_LINK_TYPE, VILLAGE_MODEL))
        self.assertFalse(is_valid_for(CLAIM_ADMIN_UBA_LINK_TYPE, VILLAGE_MODEL))

        codes = [t.code for t in get_uba_link_types(HEALTH_FACILITY_MODEL)]
        self.assertIn(CLAIM_ADMIN_UBA_LINK_TYPE, codes)
        self.assertNotIn(ENROLMENT_UBA_LINK_TYPE, codes)
