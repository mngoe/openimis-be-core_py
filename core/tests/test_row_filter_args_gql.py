import json
from dataclasses import dataclass

from graphql_jwt.shortcuts import get_token

from core.models import Officer, Role, User
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase
from core.test_helpers import create_test_interactive_user, create_test_officer, create_test_role


@dataclass
class DummyContext:
    user: User


class CoreRowFilterArgsTest(openIMISGraphQLTestCase):
    """role / enrolmentOfficers: validity and audits applied by the models (OP-2029).

    The resolvers state what their legacy arguments meant; isDeleted/showHistory
    are new and override them. Officer connections whose resolver never filtered
    (enrolmentOfficers, insureeOfficers...) keep hiding deleted officers: the
    filter OfficerGQLType used to apply is now Officer's default.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = create_test_interactive_user(username="coreRowArgsAdmin")
        cls.token = get_token(cls.admin, DummyContext(user=cls.admin))
        cls.live_role = create_test_role(name="RFAROLE live")
        cls.deleted_role = create_test_role(name="RFAROLE deleted")
        Role.objects.filter(id=cls.deleted_role.id).update(validity_to="2020-01-01")
        create_test_officer(custom_props={"code": "RFACEO1"})
        deleted = create_test_officer(custom_props={"code": "RFACEO2"})
        Officer.objects.filter(id=deleted.id).update(validity_to="2020-01-01")

    def _values(self, field, args, attr):
        response = self.query(
            '{ %s(%s) { edges { node { %s } } } }' % (field, args, attr),
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.token}"})
        self.assertResponseNoErrors(response)
        return sorted(e["node"][attr] for e in json.loads(response.content)["data"][field]["edges"])

    def test_role_default_hides_deleted(self):
        self.assertEqual(["RFAROLE live"], self._values("role", 'str: "RFAROLE"', "name"))

    def test_role_show_history_keeps_its_legacy_meaning(self):
        self.assertEqual(["RFAROLE deleted", "RFAROLE live"],
                         self._values("role", 'str: "RFAROLE", showHistory: true', "name"))

    def test_role_is_deleted(self):
        self.assertEqual(["RFAROLE deleted"], self._values("role", 'str: "RFAROLE", isDeleted: true', "name"))

    def test_enrolment_officers_default_hides_deleted(self):
        self.assertEqual(["RFACEO1"], self._values("enrolmentOfficers", 'str: "RFACEO"', "code"))
        self.assertEqual(["RFACEO2"], self._values("enrolmentOfficers", 'str: "RFACEO", isDeleted: true', "code"))


class InteractiveUsersResolverTest(openIMISGraphQLTestCase):
    """interactiveUsers runs its resolver (it used to be skipped: wrong method name).

    The field is declared `interactiveUsers`; graphene looks up
    `resolve_interactiveUsers`, so `resolve_interactive_users` was never called:
    no right was checked and deleted users and history copies were listed.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from core.models import InteractiveUser
        cls.admin = create_test_interactive_user(username="iuRowArgsAdmin")
        cls.token = get_token(cls.admin, DummyContext(user=cls.admin))
        no_right = create_test_interactive_user(
            username="iuRowArgsNoRight", roles=[create_test_role(name="iuRowArgs-no-right").id])
        cls.no_right_token = get_token(no_right, DummyContext(user=no_right))
        create_test_interactive_user(username="iuRowArgsLive", custom_props={"last_name": "RfaIuser"})
        deleted = create_test_interactive_user(username="iuRowArgsDeleted", custom_props={"last_name": "RfaIuser"})
        InteractiveUser.objects.filter(id=deleted.i_user.id).update(validity_to="2020-01-01")

    def _query(self, args, token=None):
        return self.query(
            '{ interactiveUsers(lastName_Icontains: "RfaIuser"%s) { edges { node { loginName } } } }' % args,
            headers={"HTTP_AUTHORIZATION": f"Bearer {token or self.token}"})

    def _logins(self, args=""):
        response = self._query(args)
        self.assertResponseNoErrors(response)
        edges = json.loads(response.content)["data"]["interactiveUsers"]["edges"]
        return sorted(e["node"]["loginName"] for e in edges)

    def test_the_users_right_is_checked(self):
        content = json.loads(self._query("", self.no_right_token).content)
        self.assertTrue(content.get("errors"))
        self.assertIsNone((content.get("data") or {}).get("interactiveUsers"))

    def test_default_hides_deleted_users(self):
        self.assertEqual(["iuRowArgsLive"], self._logins())

    def test_show_history_keeps_its_legacy_meaning(self):
        self.assertEqual(["iuRowArgsDeleted", "iuRowArgsLive"], self._logins(", showHistory: true"))

    def test_is_deleted(self):
        self.assertEqual(["iuRowArgsDeleted"], self._logins(", isDeleted: true"))
