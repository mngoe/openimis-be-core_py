"""
Row security mechanics in core (ported from the openIMIS reference
core/tests/test_row_security.py, adapted to Cameroon: UBA link types are
honoured and paths may be read as an ordered fallback).
"""
from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import TestCase

from core.gql.row_filter_args import row_filter_kwargs, row_filters_from_args, row_filters_in_context
from core.gql.scoped_object_type import ScopedQuerysetMixin
from core.models import Audits, Validity, target_queryset
from core.models.row_security import LocationScope, ParentScope, RowSecurityMixin
from core.test_helpers import create_right_only_user, create_security_tree


class FilterLocationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from insuree.test_helpers import create_test_insuree
        cls.tree = create_security_tree("SPRS")
        cls.user = create_right_only_user("sprsuser", [101001], [cls.tree.d1.id])
        # an insuree living in D1, and one with no current village whose family is in D2
        cls.home = create_test_insuree(
            custom_props={"chf_id": "SPRS0001", "current_village": cls.tree.v1},
            family_custom_props={"location": cls.tree.v1})
        cls.orphan = create_test_insuree(
            custom_props={"chf_id": "SPRS0002", "current_village": cls.tree.v2},
            family_custom_props={"location": cls.tree.v2})
        # the helper copies a None village onto the family: clear it afterwards
        type(cls.orphan).objects.filter(pk=cls.orphan.pk).update(current_village=None)

    def setUp(self):
        cache.clear()

    def _visible(self, fallback):
        from insuree.models import Insuree
        conditions = Insuree.filter_location(
            self.user,
            paths=("current_village__parent__parent", "family__location__parent__parent"),
            fallback=fallback)
        return set(Insuree.objects.filter(chf_id__startswith="SPRS").filter(*conditions)
                   .values_list("chf_id", flat=True))

    def test_or_of_paths_lets_a_null_path_through(self):
        # documents the hole the fallback closes
        self.assertEqual({"SPRS0001", "SPRS0002"}, self._visible(fallback=False))

    def test_fallback_asks_the_family_when_the_village_is_null(self):
        self.assertEqual({"SPRS0001"}, self._visible(fallback=True))

    def test_anonymous_gets_no_rows(self):
        from insuree.models import Insuree
        conditions = Insuree.filter_location(AnonymousUser(), paths=("current_village",))
        self.assertFalse(Insuree.objects.filter(*conditions).exists())

    def test_no_declared_paths_means_no_restriction(self):
        from insuree.models import Family
        self.assertEqual([], Family.filter_location(self.user)) if Family.row_scope is None else None


class ScopeObjectsTest(TestCase):
    def test_location_scope_needs_a_path(self):
        with self.assertRaises(ValueError):
            LocationScope()

    def test_parent_scope_inherits_fallback(self):
        parent = SimpleNamespace(row_scope=LocationScope("a__b", "c__d", fallback=True))
        scope = ParentScope("parent")
        field = SimpleNamespace(related_model=parent)
        child = SimpleNamespace(_meta=SimpleNamespace(get_field=lambda name: field))
        parent.row_scope.location_paths = lambda model: ("a__b", "c__d")
        self.assertEqual(("parent__a__b", "parent__c__d"), scope.location_paths(child))
        self.assertTrue(scope.fallback)


class GraphQLArgumentMappingTest(TestCase):
    """OP-3317 phase 2: old connection arguments -> validity / audits."""

    def test_show_history_asks_for_audits(self):
        self.assertEqual(Audits.ALL, row_filters_from_args({"showHistory": True})["audits"])

    def test_is_deleted_maps_to_validity(self):
        self.assertEqual(Validity.DELETED_NOW, row_filters_from_args({"isDeleted": True})["validity"])
        self.assertEqual(Validity.NOW, row_filters_from_args({"isDeleted": False})["validity"])
        self.assertEqual(Validity.ALL, row_filters_from_args({"isDeleted": None})["validity"])

    def test_nothing_asked_maps_to_nothing(self):
        self.assertEqual({}, row_filters_from_args({"first": 10}))

    def test_dates_only_reach_business_validity_models(self):
        info = SimpleNamespace(context=SimpleNamespace())
        business = SimpleNamespace(get_business_validity_fields=lambda: ("date_valid_from", "date_valid_to"))
        plain = SimpleNamespace(get_business_validity_fields=lambda: None)
        with row_filters_in_context(info, {"dateValidFrom__Gte": "2020-01-01"}):
            self.assertEqual("between", row_filter_kwargs(business, info)["validity"].kind)
            self.assertEqual({}, row_filter_kwargs(plain, info))
        self.assertEqual({}, row_filter_kwargs(business, info))

    def test_scoped_type_passes_the_mapping_to_the_model(self):
        calls = []

        class Model:
            @classmethod
            def get_queryset(cls, queryset, user, *, validity=None, audits=None):
                calls.append((user, validity, audits))
                return queryset

            @classmethod
            def get_business_validity_fields(cls):
                return None

        class Type(ScopedQuerysetMixin):
            _meta = SimpleNamespace(model=Model)

        user = object()
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        with row_filters_in_context(info, {"showHistory": True}):
            Type.get_queryset("qs", info)
        Type.get_queryset("qs", info)
        self.assertEqual([(user, None, Audits.ALL), (user, None, None)], calls)

    def test_an_old_signature_still_works(self):
        class Model:
            @classmethod
            def get_queryset(cls, queryset, user):
                return queryset

        class Type(ScopedQuerysetMixin):
            _meta = SimpleNamespace(model=Model)

        info = SimpleNamespace(context=SimpleNamespace(user=None))
        with row_filters_in_context(info, {"showHistory": True}):
            self.assertEqual("qs", Type.get_queryset("qs", info))


class TargetQuerysetTest(TestCase):
    def test_target_is_the_readable_rows(self):
        class Model(RowSecurityMixin):
            objects = SimpleNamespace(all=lambda: "all")

            @classmethod
            def get_queryset(cls, queryset, user=None, **rows):
                return f"{queryset}-for-{user}"

        self.assertEqual("all-for-u", target_queryset(Model, "u"))


class LatestHistoryIdsTest(TestCase):
    """Audits.at(date): each entity's latest history row, set-based (review of
    openimis/openimis-be-core_py#496), on both the PostgreSQL and the window path."""

    @classmethod
    def setUpTestData(cls):
        from core.test_helpers import create_test_interactive_user
        from tasks_management.models import TaskGroup
        cls.user = create_test_interactive_user(username="sp_hist_user")
        cls.rows = []
        for name in ("A", "B"):
            row = TaskGroup(code=f"SPH{name}0", completion_policy="ALL")
            row.save(user=cls.user)
            for version in (1, 2):
                row.code = f"SPH{name}{version}"
                row.save(user=cls.user)
            cls.rows.append(row)

    def _latest(self):
        from tasks_management.models import TaskGroup
        history = TaskGroup.history.filter(id__in=[r.id for r in self.rows])
        self.assertEqual(6, history.count())
        ids = RowSecurityMixin._latest_history_ids(history, "id")
        return sorted(history.filter(history_id__in=ids).values_list("code", flat=True))

    def test_postgresql_distinct_on(self):
        from django.db import connection
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL only")
        self.assertEqual(["SPHA2", "SPHB2"], self._latest())

    def test_window_function_path(self):
        from unittest import mock
        from django.db import connection
        with mock.patch.object(connection, "vendor", "other"):
            self.assertEqual(["SPHA2", "SPHB2"], self._latest())
