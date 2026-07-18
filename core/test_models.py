from django.test import TestCase
from .models import User, TechnicalUser, InteractiveUser
import json
import os
from datetime import datetime as py_datetime, timedelta
from unittest import mock
from django.test import TestCase
from core.models import ModuleConfiguration
from core import datetimedelta

class UserTestCase(TestCase):

    def test_t_user_active_status(self):
        always_valid = User(username='always_valid',
                            t_user=TechnicalUser(username='always_valid'))
        self.assertTrue(always_valid.is_active)

        
        not_yet_active = User(username='not_yet_active',
                              t_user=TechnicalUser(username='not_yet_active',
                                                   validity_from=py_datetime.now()+datetimedelta(days=1)))
        self.assertFalse(not_yet_active.is_active)

        not_active_anymore = User(username='not_active_anymore',
                                  t_user=TechnicalUser(username='not_active_anymore',
                                                       validity_to=py_datetime.now()+datetimedelta(days=-1)))
        self.assertFalse(not_active_anymore.is_active)

    def test_i_active_status(self):
        always_valid = User(username='always_valid',
                            i_user=InteractiveUser(login_name='always_valid'))
        self.assertTrue(always_valid.is_active)

        not_yet_active = User(username='always_valid',
                              i_user=InteractiveUser(login_name='not_yet_active',
                                                     validity_from=py_datetime.now()+datetimedelta(days=1)))
        self.assertFalse(not_yet_active.is_active)

        not_active_anymore = User(username='always_valid',
                                  i_user=InteractiveUser(login_name='not_active_anymore',
                                                         validity_to=py_datetime.now()+datetimedelta(days=-1)))
        self.assertFalse(not_active_anymore.is_active)


CACHE_PATCH_TARGET = f"{ModuleConfiguration.__module__}.cache"
class GetOrDefaultTestCase(TestCase):
    """Tests de ModuleConfiguration.get_or_default"""

    def setUp(self) -> None:
        super().setUp()
        self.module = "test_module"
        self.layer = "be"
        self.default = {"key_default": "default_value"}
        self.cache_key = f"module_config:{self.layer}:{self.module}"

    def tearDown(self) -> None:
        super().tearDown()
        # Nettoyage explicite au cas où un test toucherait le vrai cache
        with mock.patch("django.core.cache") as mocked_cache:
            pass

    # ---- NO_DATABASE ----

    @mock.patch.dict(os.environ, {"NO_DATABASE": "True"})
    def test_no_database_env_returns_default_without_touching_cache_or_db(self):
        with mock.patch("django.core.cache") as mocked_cache:
            result = ModuleConfiguration.get_or_default(
                self.module, self.default, layer=self.layer
            )

        self.assertEqual(result, self.default)
        mocked_cache.get.assert_not_called()
        mocked_cache.set.assert_not_called()

    # ---- Cache hit ----

    def test_cache_hit_returns_cached_value_without_db_query(self):
        cached_value = {"key_default": "cached_value"}

        with mock.patch("django.core.cache") as mocked_cache:
            mocked_cache.get.return_value = cached_value
            with mock.patch.object(
                ModuleConfiguration, "objects"
            ) as mocked_manager:
                result = ModuleConfiguration.get_or_default(
                    self.module, self.default, layer=self.layer
                )

        self.assertEqual(result, cached_value)
        mocked_cache.get.assert_called_once_with(self.cache_key)
        mocked_manager.filter.assert_not_called()

    # ---- Cache miss, entrée DB existante ----

    def test_cache_miss_db_entry_found_merges_with_default_and_sets_cache(self):
        db_config = {"key_default": "db_value", "extra_key": "extra_value"}
        instance = ModuleConfiguration.objects.create(
            module=self.module,
            layer=self.layer,
            version="1.0",
            config=json.dumps(db_config),
            is_exposed=False,
        )

        with mock.patch("django.core.cache") as mocked_cache:
            mocked_cache.get.return_value = None

            result = ModuleConfiguration.get_or_default(
                self.module, self.default, layer=self.layer
            )

        expected = {**self.default, **db_config}
        self.assertEqual(result, expected)
        mocked_cache.set.assert_called_once()
        set_args, set_kwargs = mocked_cache.set.call_args
        self.assertEqual(set_args[0], self.cache_key)
        self.assertEqual(set_args[1], expected)

    # ---- Cache miss, aucune entrée DB ----

    def test_cache_miss_no_db_entry_returns_default_and_sets_cache(self):
        with mock.patch("django.core.cache") as mocked_cache:
            mocked_cache.get.return_value = None

            result = ModuleConfiguration.get_or_default(
                self.module, self.default, layer=self.layer
            )

        self.assertEqual(result, self.default)
        mocked_cache.set.assert_called_once_with(
            self.cache_key, self.default, timeout=mock.ANY
        )

    # ---- Entrée désactivée (is_disabled_until dans le futur) exclue ----

    def test_disabled_entry_in_future_is_excluded(self):
        future = py_datetime.now() + timedelta(days=1)
        ModuleConfiguration.objects.create(
            module=self.module,
            layer=self.layer,
            version="1.0",
            config=json.dumps({"key_default": "should_not_be_used"}),
            is_disabled_until=future,
        )

        with mock.patch("django.core.cache") as mocked_cache:
            mocked_cache.get.return_value = None

            result = ModuleConfiguration.get_or_default(
                self.module, self.default, layer=self.layer
            )

        # L'entrée est désactivée jusqu'à une date future -> on retombe sur le default
        self.assertEqual(result, self.default)

    def test_entry_with_expired_disable_date_is_included(self):
        past = py_datetime.now() - timedelta(days=1)
        db_config = {"key_default": "should_be_used"}
        ModuleConfiguration.objects.create(
            module=self.module,
            layer=self.layer,
            version="1.0",
            config=json.dumps(db_config),
            is_disabled_until=past,
        )

        with mock.patch("django.core.cache") as mocked_cache:
            mocked_cache.get.return_value = None

            result = ModuleConfiguration.get_or_default(
                self.module, self.default, layer=self.layer
            )

        self.assertEqual(result, {**self.default, **db_config})

    def test_entry_with_null_disable_date_is_included(self):
        db_config = {"key_default": "should_be_used"}
        ModuleConfiguration.objects.create(
            module=self.module,
            layer=self.layer,
            version="1.0",
            config=json.dumps(db_config),
            is_disabled_until=None,
        )

        with mock.patch("django.core.cache") as mocked_cache:
            mocked_cache.get.return_value = None

            result = ModuleConfiguration.get_or_default(
                self.module, self.default, layer=self.layer
            )

        self.assertEqual(result, {**self.default, **db_config})

    # ---- Filtrage correct par layer/module ----

    def test_only_matching_layer_and_module_are_considered(self):
        # Même module, mauvaise layer -> ne doit pas matcher
        ModuleConfiguration.objects.create(
            module=self.module,
            layer="fe",
            version="1.0",
            config=json.dumps({"key_default": "wrong_layer"}),
        )
        # Mauvais module, bonne layer -> ne doit pas matcher
        ModuleConfiguration.objects.create(
            module="other_module",
            layer=self.layer,
            version="1.0",
            config=json.dumps({"key_default": "wrong_module"}),
        )

        with mock.patch("django.core.cache") as mocked_cache:
            mocked_cache.get.return_value = None

            result = ModuleConfiguration.get_or_default(
                self.module, self.default, layer=self.layer
            )

        self.assertEqual(result, self.default)

    # ---- Exception pendant la requête DB ----

    def test_exception_during_db_query_returns_default_and_does_not_set_cache(self):
        with mock.patch("django.core.cache") as mocked_cache:
            mocked_cache.get.return_value = None
            with mock.patch.object(
                ModuleConfiguration.objects, "filter", side_effect=Exception("db down")
            ):
                result = ModuleConfiguration.get_or_default(
                    self.module, self.default, layer=self.layer
                )

        self.assertEqual(result, self.default)
        mocked_cache.set.assert_not_called()

    # ---- Layer par défaut ----

    def test_default_layer_is_be(self):
        db_config = {"key_default": "be_value"}
        ModuleConfiguration.objects.create(
            module=self.module,
            layer="be",
            version="1.0",
            config=json.dumps(db_config),
        )

        with mock.patch("django.core.cache") as mocked_cache:
            mocked_cache.get.return_value = None
            result = ModuleConfiguration.get_or_default(self.module, self.default)

        self.assertEqual(result, {**self.default, **db_config})
        mocked_cache.get.assert_called_once_with(f"module_config:be:{self.module}")


class SaveMethodTestCase(TestCase):
    """Tests de ModuleConfiguration.save"""

    def test_save_persists_instance(self):
        instance = ModuleConfiguration(
            module="save_module",
            layer="be",
            version="1.0",
            config=json.dumps({"a": 1}),
        )
        instance.save()

        self.assertIsNotNone(instance.id)
        reloaded = ModuleConfiguration.objects.get(id=instance.id)
        self.assertEqual(reloaded.module, "save_module")

    def test_save_invalidates_cache_key(self):
        instance = ModuleConfiguration(
            module="save_module",
            layer="be",
            version="1.0",
            config=json.dumps({"a": 1}),
        )
        expected_key = "module_config:be:save_module"

        with mock.patch("django.core.cache") as mocked_cache:
            instance.save()
            mocked_cache.delete.assert_called_once_with(expected_key)

    def test_save_invalidates_correct_key_on_update(self):
        instance = ModuleConfiguration.objects.create(
            module="save_module",
            layer="fe",
            version="1.0",
            config=json.dumps({"a": 1}),
        )
        expected_key = "module_config:fe:save_module"

        with mock.patch("django.core.cache") as mocked_cache:
            instance.version = "2.0"
            instance.save()
            mocked_cache.delete.assert_called_once_with(expected_key)


class DeleteMethodTestCase(TestCase):
    """Tests de ModuleConfiguration.delete"""

    def setUp(self) -> None:
        super().setUp()
        self.instance = ModuleConfiguration.objects.create(
            module="delete_module",
            layer="be",
            version="1.0",
            config=json.dumps({"a": 1}),
        )

    def test_delete_removes_instance_from_db(self):
        instance_id = self.instance.id
        self.instance.delete()

        self.assertFalse(
            ModuleConfiguration.objects.filter(id=instance_id).exists()
        )

    def test_delete_invalidates_cache_key(self):
        expected_key = "module_config:be:delete_module"

        with mock.patch("django.core.cache") as mocked_cache:
            self.instance.delete()
            mocked_cache.delete.assert_called_once_with(expected_key)

    def test_delete_invalidates_cache_before_removal_from_db(self):
        # On vérifie l'ordre : le cache doit être invalidé, que la suppression
        # DB réussisse ou non (le code appelle cache.delete avant super().delete()).
        call_order = []

        def fake_cache_delete(key):
            call_order.append("cache_delete")

        with mock.patch("django.core.cache") as mocked_cache:
            mocked_cache.delete.side_effect = fake_cache_delete
            with mock.patch(
                "django.db.models.Model.delete",
                side_effect=lambda *a, **k: call_order.append("db_delete"),
            ):
                self.instance.delete()

        self.assertEqual(call_order, ["cache_delete", "db_delete"])
