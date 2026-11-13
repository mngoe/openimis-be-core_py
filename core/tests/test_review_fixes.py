"""
Fixes from the automated review of mngoe/openimis-be-core_py#89.
"""
import datetime as py_datetime
import os
from unittest import mock

from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from core.abs_calculation_rule import AbsStrategy
from core.models import Role, RoleRight
from core.schema import duplicate_role
from core.services.userServices import _business_object_pk
from core.test_helpers import create_test_interactive_user, create_test_role


class CreateSuperuserPasswordTest(TestCase):
    """The password comes from DJANGO_SUPERUSER_PASSWORD or the prompt, never blank."""

    def test_core_command_wins_over_django_auth(self):
        self.assertEqual("core", get_commands()["createsuperuser"])

    def test_noinput_without_password_refused(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DJANGO_SUPERUSER_PASSWORD", None)
            with self.assertRaisesMessage(CommandError, "DJANGO_SUPERUSER_PASSWORD"):
                call_command("createsuperuser", "--noinput", username="x_noinput")

    def test_blank_password_from_environment_refused(self):
        with mock.patch.dict(os.environ, {"DJANGO_SUPERUSER_PASSWORD": "   "}):
            with self.assertRaisesMessage(CommandError, "Blank passwords"):
                call_command("createsuperuser", "--noinput", username="x_blank")

    def test_blank_password_typed_refused(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DJANGO_SUPERUSER_PASSWORD", None)
            with mock.patch("getpass.getpass", side_effect=["  ", "  "]):
                with self.assertRaisesMessage(CommandError, "Blank passwords"):
                    call_command("createsuperuser", interactive=True, username="x_typed")

    def test_passwords_not_matching_refused(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DJANGO_SUPERUSER_PASSWORD", None)
            with mock.patch("getpass.getpass", side_effect=["Secret-123", "Secret-124"]):
                with self.assertRaisesMessage(CommandError, "didn't match"):
                    call_command("createsuperuser", interactive=True, username="x_typo")


class CalculationRuleValidityTest(SimpleTestCase):

    def _rule(self, valid_from, valid_to):
        return type("Rule", (AbsStrategy,), {
            "date_valid_from": valid_from, "date_valid_to": valid_to, "status": "active"})

    def test_no_start_date_is_valid(self):
        rule = self._rule(None, None)
        rule.ready()
        self.assertEqual("active", rule.status)

    def test_ended_rule_inactive(self):
        rule = self._rule(None, py_datetime.datetime(2000, 1, 1))
        rule.ready()
        self.assertEqual("inactive", rule.status)

    def test_future_rule_inactive(self):
        rule = self._rule(py_datetime.datetime(2999, 1, 1), None)
        rule.ready()
        self.assertEqual("inactive", rule.status)


class DuplicateRoleRightsTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = create_test_interactive_user(username="dup_role_admin")
        cls.role = create_test_role(name="ReviewDupSource", rights=[101101, 101102])

    def _rights(self, role):
        return set(RoleRight.objects.filter(role=role, uba=False, validity_to__isnull=True)
                   .values_list("right_id", flat=True))

    def test_rights_omitted_copied(self):
        copy = duplicate_role({"uuid": self.role.uuid, "name": "ReviewDupCopy"}, self.user)
        self.assertEqual({101101, 101102}, self._rights(copy))

    def test_empty_rights_give_a_role_without_rights(self):
        copy = duplicate_role({"uuid": self.role.uuid, "name": "ReviewDupEmpty", "rights_id": []}, self.user)
        self.assertEqual(set(), self._rights(copy))
        self.assertEqual({101101, 101102}, self._rights(Role.objects.get(id=self.role.id)))


class BusinessObjectPkTest(TestCase):

    def test_reference_that_is_not_a_uuid_tried_as_pk(self):
        role = create_test_role(name="ReviewPkRole")
        self.assertEqual(role.id, _business_object_pk("core.role", str(role.id)))
        self.assertEqual(role.id, _business_object_pk("core.role", role.uuid))

    def test_unknown_reference_is_none(self):
        self.assertIsNone(_business_object_pk("core.role", "not-a-uuid"))


class InteractiveUserHelperTest(TestCase):

    def test_existing_user_without_interactive_user_linked(self):
        from core.models import User
        User.objects.create(username="review_orphan")
        user = create_test_interactive_user(username="review_orphan")
        self.assertIsNotNone(User.objects.get(id=user.id).i_user_id)
