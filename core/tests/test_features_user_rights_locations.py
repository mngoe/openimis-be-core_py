"""
Tests for backend/features/user-rights-locations.feature, core part.
"""
import unittest
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import User
from core.test_helpers import create_test_interactive_user
from core.views import UserViewSet

GAP = "claim_admin/officer in current_user (#36195, core PR 55) is not in this checkout: it is on origin/dev-csu"


class CurrentUserPayloadTest(TestCase):
    def _current_user(self, user):
        request = APIRequestFactory().get("/core/users/current_user/")
        force_authenticate(request, user=user)
        return UserViewSet.as_view({"get": "current_user"})(request)

    # Scenario: The current user payload keeps its structure and adds claim_admin and officer (@redmine-36195)
    def test_payload_structure(self):
        user = create_test_interactive_user(username="featcuplain")
        response = self._current_user(user)
        self.assertEqual(response.status_code, 200)
        for key in ("id", "username", "i_user", "t_user"):
            self.assertIn(key, response.data)
        self.assertIn("rights", response.data["i_user"])
        self.assertIn("claim_admin", response.data, GAP)
        self.assertIn("officer", response.data, GAP)
        self.assertIsNone(response.data["claim_admin"])
        self.assertIsNone(response.data["officer"])

    # Scenario: The current user payload of a claim administrator includes the health facility (@redmine-36195)
    def test_claim_admin_payload(self):
        from claim.test_helpers import create_test_claim_admin
        base = create_test_interactive_user(username="featcuca")
        from core.test_helpers import create_security_tree
        hf = create_security_tree("FCUA").hf1
        claim_admin = create_test_claim_admin(custom_props={"code": "FEATCUCA", "health_facility_id": hf.id})
        user = User.objects.create(username="featcucauser", i_user=base.i_user, claim_admin=claim_admin)
        response = self._current_user(user)
        self.assertEqual(response.status_code, 200)
        self.assertIn("claim_admin", response.data, GAP)
        self.assertEqual(response.data["claim_admin"]["code"], "FEATCUCA")
        self.assertEqual(response.data["claim_admin"]["healthFacility"]["code"], claim_admin.health_facility.code)
        self.assertIsNone(response.data["officer"])


class HealthFacilityProgramSaveTest(TestCase):
    # Scenario: A health facility saves with a new price list and the Chèque Santé program (@redmine-33973)
    def test_hf_saves_with_program_and_pricelist(self):
        from location.models import HealthFacility
        from location.test_helpers import create_test_village, create_test_health_facility
        from medical_pricelist.test_helpers import create_test_service_pricelist
        from program.test_helpers import create_test_program
        village = create_test_village()
        hf = create_test_health_facility("FHFP1", village.parent.parent.id, valid=True)
        hf.save_history()
        hf.services_pricelist = create_test_service_pricelist(village.parent.parent.id)
        hf.save()
        hf.program.add(create_test_program(code="CCS", name="Chèque Santé"))
        hf = HealthFacility.objects.get(id=hf.id)
        self.assertEqual(hf.program.count(), 1)
        self.assertIsNotNone(hf.services_pricelist_id)


class UserBusinessAccessTest(TestCase):
    """#38161 is still open in Redmine, but the UBA work exists in this checkout, so the test runs."""
    LINK = "FEAT_CLAIM_ADMIN"

    def setUp(self):
        from django.core.cache import cache
        from core.test_helpers import create_test_officer, create_test_role
        from core.uba_link_types import register_uba_link_type, unregister_uba_link_type
        cache.clear()
        register_uba_link_type(self.LINK, "Claim admin", models=("core.officer",))
        self.addCleanup(unregister_uba_link_type, self.LINK)
        self.addCleanup(cache.clear)
        self.obj = create_test_officer(valid=True, custom_props={"code": "FUBAOFF1"})
        self.other = create_test_officer(valid=True, custom_props={"code": "FUBAOFF2"})
        role = create_test_role(name="FEAT UBA role", uba_rights=[111002])
        self.user = create_test_interactive_user(username="featubauser", roles=[role.id])

    # Scenario: A UBA right applies only on the linked business object (@redmine-38161)
    def test_uba_right_only_on_linked_object(self):
        from core.models import RoleRight
        from core.test_helpers import create_test_user_business_access
        self.assertFalse(RoleRight._meta.get_field("uba").default)
        create_test_user_business_access(user=self.user, business_object=self.obj, link_type=self.LINK)
        self.assertFalse(self.user.has_perms(["111002"]))
        self.assertTrue(self.user.has_perms(["111002"], access_requirements=["core.officer", self.obj.uuid]))
        self.assertFalse(self.user.has_perms(["111002"], access_requirements=["core.officer", self.other.uuid]))


class CommunityDecoratorTestsCi(TestCase):
    # Scenario: The community tests of the location decorator fix pass in the CSU CI (@redmine-37294)
    @unittest.skip("open: #37294")
    def test_community_decorator_tests_pass(self):
        pass
