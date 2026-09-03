"""
Coverage of `core.uba_filters`, the row level filter the modules build from the UBA links.

The helper is deliberately tested on `core.officer`: it is a model core owns, so the
prefix behaviour ('' when the model *is* the linked object, a path when it is not) can be
exercised without depending on claim, insuree or policy.
"""
from django.core.cache import cache
from django.test import TestCase

from core import datetime, datetimedelta
from core.models import UserBusinessAccess
from core.test_helpers import (
    create_test_interactive_user,
    create_test_officer,
    create_test_user_business_access,
)
from core.uba_filters import (
    build_uba_filter_query,
    business_access_object_ids,
    has_business_access_links,
)
from core.uba_link_types import register_uba_link_type, unregister_uba_link_type
from location.models import OfficerVillage
from location.test_helpers import create_test_village

_LINK_TYPE = "TEST_FILTER_ACCOUNTANT"
_OTHER_LINK_TYPE = "TEST_FILTER_AUDITOR"


class UbaFilterQueryTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        register_uba_link_type(_LINK_TYPE, "Accountant", models=("core.officer",))
        register_uba_link_type(_OTHER_LINK_TYPE, "Auditor", models=("core.officer",))
        cls.linked_officer = create_test_officer(valid=True, custom_props={"code": "UBAF1"})
        cls.other_officer = create_test_officer(valid=True, custom_props={"code": "UBAF2"})
        cls.village = create_test_village({"name": "UbaFilter"})
        # one OfficerVillage per officer, so a filter reaching the officer through a path
        # can be told apart from one that matched everything
        cls.linked_village_row = OfficerVillage.objects.create(
            officer=cls.linked_officer, location=cls.village, audit_user_id=-1)
        cls.other_village_row = OfficerVillage.objects.create(
            officer=cls.other_officer, location=cls.village, audit_user_id=-1)
        cls.user = create_test_interactive_user(username="ubafilteruser")

    @classmethod
    def tearDownClass(cls):
        unregister_uba_link_type(_LINK_TYPE)
        unregister_uba_link_type(_OTHER_LINK_TYPE)
        super().tearDownClass()

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()
        UserBusinessAccess.objects.all().delete()

    def _link(self, business_object=None, link_type=_LINK_TYPE, **custom_props):
        return create_test_user_business_access(
            user=self.user,
            business_object=business_object or self.linked_officer,
            link_type=link_type,
            custom_props=custom_props or None,
        )

    # --- no link: the caller must be told to keep its own row security ---------- #

    def test_returns_none_without_any_link(self):
        self.assertIsNone(build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer"))

    def test_returns_none_when_only_another_link_type_is_held(self):
        self._link(link_type=_OTHER_LINK_TYPE)
        self.assertIsNone(build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer"))

    def test_none_is_returned_even_when_a_queryset_is_passed(self):
        # None rather than an empty queryset: filtering everything out would silently
        # hide the rows a non linked user is entitled to through the location filter
        self.assertIsNone(build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer",
            queryset=OfficerVillage.objects.all()))

    # --- a link narrows, on the model itself and through a path ----------------- #

    def test_prefix_reaches_the_linked_object_through_a_path(self):
        self._link()
        rows = build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer",
            prefix="officer", queryset=OfficerVillage.objects.all())
        self.assertEqual([row.id for row in rows], [self.linked_village_row.id])

    def test_empty_prefix_filters_on_the_primary_key(self):
        self._link()
        from core.models import Officer

        officers = build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer",
            queryset=Officer.objects.all())
        self.assertEqual([o.id for o in officers], [self.linked_officer.id])

    def test_several_prefixes_are_ored(self):
        self._link()
        # 'officer' matches the linked row, 'location__officer_villages__officer' matches
        # every row of the village: the union is both, so the paths are OR'ed
        rows = build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer",
            prefix=["officer", "location__officer_villages__officer"],
            queryset=OfficerVillage.objects.all())
        self.assertEqual(
            {self.linked_village_row.id, self.other_village_row.id},
            {row.id for row in rows.distinct()},
        )

    def test_link_on_another_object_does_not_match(self):
        self._link(business_object=self.other_officer)
        rows = build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer",
            prefix="officer", queryset=OfficerVillage.objects.all())
        self.assertEqual([row.id for row in rows], [self.other_village_row.id])

    def test_several_links_widen_the_narrowing(self):
        self._link()
        self._link(business_object=self.other_officer)
        rows = build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer",
            prefix="officer", queryset=OfficerVillage.objects.all())
        self.assertEqual(
            {self.linked_village_row.id, self.other_village_row.id},
            {row.id for row in rows},
        )

    def test_include_null_keeps_the_rows_without_a_link_target(self):
        self._link()
        without = build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer", prefix="officer")
        with_null = build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer",
            prefix="officer", include_null=True)
        self.assertNotEqual(str(without), str(with_null))
        self.assertIn("officer__isnull", str(with_null))

    # --- the model label may be left to the registry ---------------------------- #

    def test_model_label_can_be_derived_from_the_registry(self):
        self._link()
        from core.models import Officer

        officers = build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, queryset=Officer.objects.all())
        self.assertEqual([o.id for o in officers], [self.linked_officer.id])

    # --- only active links inside their validity window count ------------------- #

    def test_inactive_link_does_not_narrow(self):
        self._link(active=False)
        self.assertIsNone(build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer"))

    def test_expired_link_does_not_narrow(self):
        now = datetime.datetime.now()
        self._link(date_valid_to=now - datetimedelta(days=1))
        self.assertIsNone(build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer"))

    def test_future_link_does_not_narrow_yet(self):
        now = datetime.datetime.now()
        self._link(date_valid_from=now + datetimedelta(days=1))
        self.assertIsNone(build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer"))

    def test_link_of_another_user_does_not_narrow(self):
        other_user = create_test_interactive_user(username="ubafilterother")
        create_test_user_business_access(
            user=other_user, business_object=self.linked_officer, link_type=_LINK_TYPE)
        self.assertIsNone(build_uba_filter_query(
            self.user, link_types=_LINK_TYPE, model_label="core.officer"))

    # --- the building blocks ---------------------------------------------------- #

    def test_business_access_object_ids_returns_sorted_ints(self):
        self._link()
        self._link(business_object=self.other_officer)
        self.assertEqual(
            sorted([self.linked_officer.id, self.other_officer.id]),
            business_access_object_ids(self.user, _LINK_TYPE, "core.officer"),
        )

    def test_business_access_object_ids_is_empty_without_link(self):
        self.assertEqual([], business_access_object_ids(self.user, _LINK_TYPE, "core.officer"))

    def test_has_business_access_links(self):
        self.assertFalse(has_business_access_links(self.user, _LINK_TYPE, "core.officer"))
        self._link()
        self.assertTrue(has_business_access_links(self.user, _LINK_TYPE, "core.officer"))
