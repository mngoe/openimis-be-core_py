from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.serializers import ClaimAdminCurrentUserSerializer, OfficerCurrentUserSerializer
from core.test_helpers import create_test_interactive_user
from core.views import UserViewSet


class _ListManager:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class CurrentUserApiTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = create_test_interactive_user(
            username="current_user_api_test",
            custom_props={"email": "current_user@test.org", "phone": "+23700000000"},
        )

    def test_current_user_non_regression_and_i_user_enrichment(self):
        request = self.factory.get("/core/users/current_user/")
        force_authenticate(request, user=self.user)

        response = UserViewSet.as_view({"get": "current_user"})(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("id", response.data)
        self.assertIn("username", response.data)
        self.assertIn("i_user", response.data)
        self.assertIn("t_user", response.data)

        self.assertIn("rights", response.data["i_user"])
        self.assertIn("roles", response.data["i_user"])
        self.assertIn("email", response.data["i_user"])
        self.assertIn("phone", response.data["i_user"])
        self.assertIsInstance(response.data["i_user"]["roles"], list)

    def test_current_user_null_officer_and_claim_admin(self):
        request = self.factory.get("/core/users/current_user/")
        force_authenticate(request, user=self.user)

        response = UserViewSet.as_view({"get": "current_user"})(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("officer", response.data)
        self.assertIn("claimAdmin", response.data)
        self.assertIsNone(response.data["officer"])
        self.assertIsNone(response.data["claimAdmin"])

    def test_officer_serializer_structure(self):
        parent_location = SimpleNamespace(id=2, uuid="loc-parent", code="EST", name="Est", type="R", parent=None)
        location = SimpleNamespace(
            id=3,
            uuid="loc-child",
            code="BER",
            name="Bertoua",
            type="D",
            parent=parent_location,
        )
        officer = SimpleNamespace(
            id=11,
            uuid="off-uuid",
            code="OFF001",
            dob=None,
            address="Address",
            last_name="Sandjong",
            other_names="Paul",
            location=location,
        )

        data = OfficerCurrentUserSerializer(officer).data
        self.assertEqual(data["id"], 11)
        self.assertEqual(data["lastName"], "Sandjong")
        self.assertEqual(data["otherNames"], "Paul")
        self.assertEqual(data["location"]["parent"]["code"], "EST")

    def test_claim_admin_serializer_structure(self):
        parent_location = SimpleNamespace(id=2, uuid="loc-parent", code="EST", name="Est", type="R", parent=None)
        location = SimpleNamespace(
            id=3,
            uuid="loc-child",
            code="BER",
            name="Bertoua",
            type="D",
            parent=parent_location,
        )
        services_pricelist = SimpleNamespace(id=10, uuid="sp-uuid")
        health_facility = SimpleNamespace(
            id=56,
            uuid="hf-uuid",
            code="ES001",
            name="HR Bertoua",
            level="H",
            services_pricelist=services_pricelist,
            items_pricelist=None,
            contract_start_date=None,
            contract_end_date=None,
            location=location,
            program=_ListManager([SimpleNamespace(idProgram="1", nameProgram="VIH")]),
        )
        claim_admin = SimpleNamespace(
            id=22,
            uuid="ca-uuid",
            code="paul",
            has_login=True,
            email_id="paul@gmail.com",
            phone="",
            dob=None,
            last_name="Sandjong",
            other_names="Paul",
            health_facility=health_facility,
        )

        data = ClaimAdminCurrentUserSerializer(claim_admin).data
        self.assertEqual(data["id"], 22)
        self.assertEqual(data["hasLogin"], True)
        self.assertEqual(data["emailId"], "paul@gmail.com")
        self.assertEqual(data["healthFacility"]["code"], "ES001")
        self.assertEqual(data["healthFacility"]["program"]["edges"][0]["node"]["nameProgram"], "VIH")

    def test_current_user_adds_vih_right_without_duplicate(self):
        request = SimpleNamespace(user=SimpleNamespace(_u=SimpleNamespace(id=1)))
        serializer = SimpleNamespace(data={"i_user": {"rights": [10119]}})
        qs_with_vih = MagicMock()
        qs_with_vih.filter.return_value = qs_with_vih
        qs_with_vih.__bool__.return_value = True

        viewset = UserViewSet()
        with patch.object(UserViewSet, "get_serializer", return_value=serializer), patch(
            "core.views.program_models.Program.objects.filter", return_value=qs_with_vih
        ):
            response = viewset.current_user(request)

        self.assertEqual(response.data["i_user"]["rights"], [10119])
