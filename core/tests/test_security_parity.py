"""
Security parity tests for core (API rights + escalation) against the openimis-dev-tools
reference (core fix/setup-version-1.12.0). Each test asserts the REFERENCE behaviour:
a failure is a gap in Cameroon.

Tags: [REF <reference file>::<test>] ported/adapted, [NEW] no reference equivalent.
"""
import json
import uuid

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.apps import CoreConfig
from core.models import MutationLog, Role, User, UserBusinessAccess
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase
from core.test_helpers import (
    create_right_only_user,
    create_security_tree,
    get_or_create_test_admin_role,
    get_test_token,
    perm_ids,
)

USER_QUERY = perm_ids(CoreConfig.gql_query_users_perms)
USER_CREATE = perm_ids(CoreConfig.gql_mutation_create_users_perms)
USER_UPDATE = perm_ids(CoreConfig.gql_mutation_update_users_perms)
USER_DELETE = perm_ids(CoreConfig.gql_mutation_delete_users_perms)

CREATE_ROLE = """
mutation ($input: CreateRoleMutationInput!) {
  createRole(input: $input) { clientMutationId internalId }
}
"""


def _role_variables(name):
    return {"input": {"name": name, "isSystem": False, "isBlocked": False,
                      "clientMutationId": str(uuid.uuid4())}}


class AnonymousMutationIsRefusedEarlyTest(openIMISGraphQLTestCase):
    """[REF core/tests/test_mutation_authentication.py] An anonymous mutation must be
    refused in OpenIMISMutation.mutate_and_get_payload, before the MutationLog row is
    written (and before a Celery job is queued)."""

    def test_anonymous_mutation_returns_401(self):
        """[REF test_mutation_authentication.py::test_anonymous_mutation_returns_401]"""
        response = self.query(CREATE_ROLE, variables=_role_variables("SPAanon1"))
        self.assertEqual(401, response.status_code, response.content)

    def test_anonymous_mutation_writes_no_mutation_log(self):
        """[REF test_mutation_authentication.py::test_anonymous_mutation_writes_no_mutation_log]"""
        before = MutationLog.objects.count()
        self.query(CREATE_ROLE, variables=_role_variables("SPAanon2"))
        self.assertEqual(before, MutationLog.objects.count())

    def test_anonymous_mutation_creates_nothing(self):
        """[REF test_mutation_authentication.py::test_anonymous_mutation_creates_nothing]"""
        self.query(CREATE_ROLE, variables=_role_variables("SPAanon3"))
        self.assertFalse(Role.objects.filter(name="SPAanon3").exists())


class UserRestApiRightsTest(TestCase):
    """[REF core/tests/test_user_api_rights.py] REST /api/core/users/ (UserViewSet) maps
    each action to the user-admin right; only current_user stays open."""

    @classmethod
    def setUpTestData(cls):
        cls.nobody = create_right_only_user("spa_nobody", [])
        cls.reader = create_right_only_user("spa_reader", USER_QUERY)
        cls.victim = create_right_only_user("spa_victim", [])

    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()

    def _view(self, actions):
        from core.views import UserViewSet
        return UserViewSet.as_view(actions)

    def test_listing_accounts_requires_the_query_right(self):
        """[REF test_user_api_rights.py::test_listing_accounts_requires_the_query_right]"""
        request = self.factory.get("/api/core/users/")
        force_authenticate(request, user=self.nobody)
        response = self._view({"get": "list"})(request)
        self.assertEqual(403, response.status_code)

    def test_listing_accounts_is_allowed_with_the_query_right(self):
        """[REF test_user_api_rights.py::test_listing_accounts_is_allowed_with_the_query_right]"""
        request = self.factory.get("/api/core/users/")
        force_authenticate(request, user=self.reader)
        response = self._view({"get": "list"})(request)
        self.assertEqual(200, response.status_code)

    def test_renaming_another_account_requires_the_update_right(self):
        """[REF test_user_api_rights.py::test_a_user_cannot_promote_itself] (adapted: the
        Cameroon serializer exposes `username` as the writable field)."""
        request = self.factory.patch(
            f"/api/core/users/{self.victim.id}/", {"username": "spa_pwned"}, format="json")
        force_authenticate(request, user=self.nobody)
        response = self._view({"patch": "partial_update"})(request, pk=str(self.victim.id))
        self.assertEqual(403, response.status_code)
        self.victim.refresh_from_db()
        self.assertEqual("spa_victim", self.victim.username)

    def test_deleting_an_account_requires_the_delete_right(self):
        """[REF test_user_api_rights.py] (UserViewSet._ACTION_RIGHTS destroy -> delete right)"""
        request = self.factory.delete(f"/api/core/users/{self.victim.id}/")
        force_authenticate(request, user=self.nobody)
        response = self._view({"delete": "destroy"})(request, pk=str(self.victim.id))
        self.assertEqual(403, response.status_code)
        self.assertTrue(User.objects.filter(id=self.victim.id).exists())

    def test_creating_an_account_requires_the_create_right(self):
        """[REF test_user_api_rights.py] (UserViewSet._ACTION_RIGHTS create -> create right)"""
        request = self.factory.post("/api/core/users/", {"username": "spa_new"}, format="json")
        force_authenticate(request, user=self.nobody)
        response = self._view({"post": "create"})(request)
        self.assertEqual(403, response.status_code)
        self.assertFalse(User.objects.filter(username="spa_new").exists())

    def test_current_user_stays_open_to_any_authenticated_account(self):
        """[REF test_user_api_rights.py::test_current_user_stays_open_to_any_authenticated_account]"""
        request = self.factory.get("/api/core/users/current_user/")
        force_authenticate(request, user=self.nobody)
        response = self._view({"get": "current_user"})(request)
        self.assertEqual(200, response.status_code)


class ScheduledJobsViewTest(TestCase):
    """[NEW] /api/core/scheduled_jobs exposes the scheduler internals (job names,
    handlers): it must at least require authentication."""

    def test_scheduled_jobs_requires_authentication(self):
        from core.views import get_scheduled_jobs
        request = APIRequestFactory().get("/api/core/scheduled_jobs")
        response = get_scheduled_jobs(request)
        self.assertIn(response.status_code, (401, 403))


class UserMutationEscalationTest(TestCase):
    """[REF core/schema.py update_or_create_user: "only a superuser may hand out the
    superuser flag"] In Cameroon the superuser flag is the IMIS Administrator role
    (is_system=64): a user-admin who is not themselves admin must not grant it."""

    @classmethod
    def setUpTestData(cls):
        cls.tree = create_security_tree("SPA")
        cls.admin_role = get_or_create_test_admin_role()
        cls.plain_role = Role.objects.create(name="SPA plain", is_system=0, is_blocked=False,
                                             audit_user_id=-1)
        cls.user_admin = create_right_only_user(
            "spaua", USER_QUERY + USER_CREATE + USER_UPDATE + USER_DELETE,
            [cls.tree.d1.id], custom_props={"email": "spaua@example.org"})
        cls.target = create_right_only_user(
            "spa_tgt", [], [cls.tree.d1.id], custom_props={"email": "spa_tgt@example.org"})

    def setUp(self):
        cache.clear()

    def _user_payload(self, username, roles, **extra):
        return {
            "username": username, "last_name": "Sp", "other_names": "A",
            "email": f"{username}@example.org", "language": "en",
            "roles": roles, "districts": [self.tree.d1.id], "uuid": None, **extra,
        }

    def test_control_a_user_admin_can_create_a_plain_user(self):
        """Control: the payload is valid, so a refusal below is the security check."""
        from core.schema import CreateUserMutation
        errors = CreateUserMutation.async_mutate(
            self.user_admin, **self._user_payload("spa_ctl1", [self.plain_role.id]))
        self.assertFalse(errors, errors)
        self.assertTrue(User.objects.filter(username="spa_ctl1").exists())

    def test_a_non_admin_cannot_create_an_administrator(self):
        from core.schema import CreateUserMutation
        errors = CreateUserMutation.async_mutate(
            self.user_admin, **self._user_payload("spa_esc1", [self.admin_role.id]))
        created = User.objects.filter(username="spa_esc1").first()
        self.assertTrue(errors, "creating an IMIS Administrator must be refused")
        self.assertTrue(created is None or not created.is_imis_admin)

    def test_a_non_admin_cannot_promote_themself(self):
        from core.schema import UpdateUserMutation
        errors = UpdateUserMutation.async_mutate(
            self.user_admin, **self._user_payload(
                "spaua", [self.admin_role.id], uuid=str(self.user_admin.id)))
        cache.clear()
        me = User.objects.get(id=self.user_admin.id)
        self.assertFalse(me.is_imis_admin, "user-admin promoted itself to IMIS Administrator")

    def test_granting_a_uba_link_requires_the_uba_create_right(self):
        """[NEW] gql_mutation_create_user_business_access_perms is declared but never
        checked: business_accesses ride on Create/UpdateUser with only the user rights."""
        from core.apps import ENROLMENT_UBA_LINK_TYPE
        from core.schema import UpdateUserMutation
        UpdateUserMutation.async_mutate(
            self.user_admin, **self._user_payload(
                "spa_tgt", [self.plain_role.id], uuid=str(self.target.id),
                business_accesses=[{"link_type": ENROLMENT_UBA_LINK_TYPE,
                                    "business_object_model": "location.location",
                                    "object_id": str(self.tree.v2.id)}]))
        self.assertFalse(
            UserBusinessAccess.objects.filter(user=self.target).exists(),
            "a UBA link was granted without the UBA create right")

    def test_a_user_cannot_delete_themselves(self):
        """[REF core/tests/test_user_delete.py::test_a_user_cannot_delete_themselves]"""
        from core.schema import DeleteUserMutation
        DeleteUserMutation.async_mutate(self.user_admin, uuids=[str(self.user_admin.id)])
        me = User.objects.get(id=self.user_admin.id)
        self.assertIsNone(me.i_user.validity_to, "a user deleted their own account")

    def test_changing_another_password_requires_the_update_right(self):
        """[REF core/services/userServices.change_user_password]"""
        from django.core.exceptions import PermissionDenied
        from core.services.userServices import change_user_password
        with self.assertRaises(PermissionDenied):
            change_user_password(self.target, username_to_update="spaua",
                                 new_password="Pwned1234!")


class MutationLogScopeTest(openIMISGraphQLTestCase):
    """[REF core/schema.py MutationLogGQLType.get_queryset] a non-superuser only sees
    their own mutation logs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.me = create_right_only_user("spa_logme", [])
        cls.other = create_right_only_user("spa_logot", [])
        MutationLog.objects.create(json_content="{}", user=cls.other,
                                   client_mutation_id="spa-other-log")
        MutationLog.objects.create(json_content="{}", user=cls.me,
                                   client_mutation_id="spa-own-log")

    def test_mutation_logs_are_limited_to_own(self):
        token = get_test_token(self.me)
        response = self.query(
            "{ mutationLogs(first: 50) { edges { node { clientMutationId } } } }",
            headers={"HTTP_AUTHORIZATION": f"Bearer {token}"})
        content = json.loads(response.content)
        ids = {e["node"]["clientMutationId"] for e in content["data"]["mutationLogs"]["edges"]}
        self.assertIn("spa-own-log", ids)
        self.assertNotIn("spa-other-log", ids)
