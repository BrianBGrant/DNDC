# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import datetime
import os
import re
import tokenize
import unittest
import warnings

from django.core.validators import RegexValidator, EmailValidator
from django.db import models, migrations
from django.db.migrations.writer import MigrationWriter, SettingsReference
from django.test import TestCase
from django.conf import settings
from django.utils import datetime_safe, six
from django.utils.deconstruct import deconstructible
from django.utils.translation import ugettext_lazy as _
from django.utils.timezone import get_default_timezone, utc, FixedOffset

import custom_migration_operations.operations
import custom_migration_operations.more_operations


class TestModel1(object):
    def upload_to(self):
        return "somewhere dynamic"
    thing = models.FileField(upload_to=upload_to)


class WriterTests(TestCase):
    """
    Tests the migration writer (makes migration files from Migration instances)
    """

    def safe_exec(self, string, value=None):
        l = {}
        try:
            exec(string, globals(), l)
        except Exception as e:
            if value:
                self.fail("Could not exec %r (from value %r): %s" % (string.strip(), value, e))
            else:
                self.fail("Could not exec %r: %s" % (string.strip(), e))
        return l

    def serialize_round_trip(self, value):
        string, imports = MigrationWriter.serialize(value)
        return self.safe_exec("%s\ntest_value_result = %s" % ("\n".join(imports), string), value)['test_value_result']

    def assertSerializedEqual(self, value):
        self.assertEqual(self.serialize_round_trip(value), value)

    def assertSerializedResultEqual(self, value, target):
        self.assertEqual(MigrationWriter.serialize(value), target)

    def assertSerializedFieldEqual(self, value):
        new_value = self.serialize_round_trip(value)
        self.assertEqual(value.__class__, new_value.__class__)
        self.assertEqual(value.max_length, new_value.max_length)
        self.assertEqual(value.null, new_value.null)
        self.assertEqual(value.unique, new_value.unique)

    def test_serialize(self):
        """
        Tests various different forms of the serializer.
        This does not care about formatting, just that the parsed result is
        correct, so we always exec() the result and check that.
        """
        # Basic values
        self.assertSerializedEqual(1)
        self.assertSerializedEqual(None)
        self.assertSerializedEqual(b"foobar")
        string, imports = MigrationWriter.serialize(b"foobar")
        self.assertEqual(string, "b'foobar'")
        self.assertSerializedEqual("föobár")
        string, imports = MigrationWriter.serialize("foobar")
        self.assertEqual(string, "'foobar'")
        self.assertSerializedEqual({1: 2})
        self.assertSerializedEqual(["a", 2, True, None])
        self.assertSerializedEqual({2, 3, "eighty"})
        self.assertSerializedEqual({"lalalala": ["yeah", "no", "maybe"]})
        self.assertSerializedEqual(_('Hello'))
        # Builtins
        self.assertSerializedEqual([list, tuple, dict, set])
        string, imports = MigrationWriter.serialize([list, tuple, dict, set])
        self.assertEqual(string, "[list, tuple, dict, set]")
        self.assertEqual(imports, set())
        # Functions
        with six.assertRaisesRegex(self, ValueError, 'Cannot serialize function: lambda'):
            self.assertSerializedEqual(lambda x: 42)
        self.assertSerializedEqual(models.SET_NULL)
        string, imports = MigrationWriter.serialize(models.SET(42))
        self.assertEqual(string, 'models.SET(42)')
        self.serialize_round_trip(models.SET(42))
        # Datetime stuff
        self.assertSerializedEqual(datetime.datetime.utcnow())
        self.assertSerializedEqual(datetime.datetime.utcnow)
        self.assertSerializedEqual(datetime.datetime.today())
        self.assertSerializedEqual(datetime.datetime.today)
        self.assertSerializedEqual(datetime.date.today())
        self.assertSerializedEqual(datetime.date.today)
        self.assertSerializedEqual(datetime.datetime.now().time())
        self.assertSerializedEqual(datetime.datetime(2014, 1, 1, 1, 1, tzinfo=get_default_timezone()))
        self.assertSerializedEqual(datetime.datetime(2014, 1, 1, 1, 1, tzinfo=FixedOffset(180)))
        safe_date = datetime_safe.date(2014, 3, 31)
        string, imports = MigrationWriter.serialize(safe_date)
        self.assertEqual(string, repr(datetime.date(2014, 3, 31)))
        self.assertEqual(imports, {'import datetime'})
        safe_datetime = datetime_safe.datetime(2014, 3, 31, 16, 4, 31)
        string, imports = MigrationWriter.serialize(safe_datetime)
        self.assertEqual(string, repr(datetime.datetime(2014, 3, 31, 16, 4, 31)))
        self.assertEqual(imports, {'import datetime'})
        timezone_aware_datetime = datetime.datetime(2012, 1, 1, 1, 1, tzinfo=utc)
        string, imports = MigrationWriter.serialize(timezone_aware_datetime)
        self.assertEqual(string, "datetime.datetime(2012, 1, 1, 1, 1, tzinfo=utc)")
        self.assertEqual(imports, {'import datetime', 'from django.utils.timezone import utc'})
        # Django fields
        self.assertSerializedFieldEqual(models.CharField(max_length=255))
        self.assertSerializedFieldEqual(models.TextField(null=True, blank=True))
        # Setting references
        self.assertSerializedEqual(SettingsReference(settings.AUTH_USER_MODEL, "AUTH_USER_MODEL"))
        self.assertSerializedResultEqual(
            SettingsReference("someapp.model", "AUTH_USER_MODEL"),
            (
                "settings.AUTH_USER_MODEL",
                {"from django.conf import settings"},
            )
        )
        self.assertSerializedResultEqual(
            ((x, x * x) for x in range(3)),
            (
                "((0, 0), (1, 1), (2, 4))",
                set(),
            )
        )

    def test_serialize_compiled_regex(self):
        """
        Make sure compiled regex can be serialized.
        """
        regex = re.compile(r'^\w+$', re.U)
        self.assertSerializedEqual(regex)

    def test_serialize_class_based_validators(self):
        """
        Ticket #22943: Test serialization of class-based validators, including
        compiled regexes.
        """
        validator = RegexValidator(message="hello")
        string = MigrationWriter.serialize(validator)[0]
        self.assertEqual(string, "django.core.validators.RegexValidator(message='hello')")
        self.serialize_round_trip(validator)

        # Test with a compiled regex.
        validator = RegexValidator(regex=re.compile(r'^\w+$', re.U))
        string = MigrationWriter.serialize(validator)[0]
        self.assertEqual(string, "django.core.validators.RegexValidator(regex=re.compile('^\\\\w+$', 32))")
        self.serialize_round_trip(validator)

        # Test a string regex with flag
        validator = RegexValidator(r'^[0-9]+$', flags=re.U)
        string = MigrationWriter.serialize(validator)[0]
        self.assertEqual(string, "django.core.validators.RegexValidator('^[0-9]+$', flags=32)")
        self.serialize_round_trip(validator)

        # Test message and code
        validator = RegexValidator('^[-a-zA-Z0-9_]+$', 'Invalid', 'invalid')
        string = MigrationWriter.serialize(validator)[0]
        self.assertEqual(string, "django.core.validators.RegexValidator('^[-a-zA-Z0-9_]+$', 'Invalid', 'invalid')")
        self.serialize_round_trip(validator)

        # Test with a subclass.
        validator = EmailValidator(message="hello")
        string = MigrationWriter.serialize(validator)[0]
        self.assertEqual(string, "django.core.validators.EmailValidator(message='hello')")
        self.serialize_round_trip(validator)

        validator = deconstructible(path="migrations.test_writer.EmailValidator")(EmailValidator)(message="hello")
        string = MigrationWriter.serialize(validator)[0]
        self.assertEqual(string, "migrations.test_writer.EmailValidator(message='hello')")

        validator = deconstructible(path="custom.EmailValidator")(EmailValidator)(message="hello")
        with six.assertRaisesRegex(self, ImportError, "No module named '?custom'?"):
            MigrationWriter.serialize(validator)

        validator = deconstructible(path="django.core.validators.EmailValidator2")(EmailValidator)(message="hello")
        with self.assertRaisesMessage(ValueError, "Could not find object EmailValidator2 in django.core.validators."):
            MigrationWriter.serialize(validator)

    def test_serialize_empty_nonempty_tuple(self):
        """
        Ticket #22679: makemigrations generates invalid code for (an empty
        tuple) default_permissions = ()
        """
        empty_tuple = ()
        one_item_tuple = ('a',)
        many_items_tuple = ('a', 'b', 'c')
        self.assertSerializedEqual(empty_tuple)
        self.assertSerializedEqual(one_item_tuple)
        self.assertSerializedEqual(many_items_tuple)

    @unittest.skipUnless(six.PY2, "Only applies on Python 2")
    def test_serialize_direct_function_reference(self):
        """
        Ticket #22436: You cannot use a function straight from its body
        (e.g. define the method and use it in the same body)
        """
        with self.assertRaises(ValueError):
            self.serialize_round_trip(TestModel1.thing)

    def test_serialize_local_function_reference(self):
        """
        Neither py2 or py3 can serialize a reference in a local scope.
        """
        class TestModel2(object):
            def upload_to(self):
                return "somewhere dynamic"
            thing = models.FileField(upload_to=upload_to)
        with self.assertRaises(ValueError):
            self.serialize_round_trip(TestModel2.thing)

    def test_serialize_local_function_reference_message(self):
        """
        Make sure user is seeing which module/function is the issue
        """
        class TestModel2(object):
            def upload_to(self):
                return "somewhere dynamic"
            thing = models.FileField(upload_to=upload_to)

        with six.assertRaisesRegex(self, ValueError,
                '^Could not find function upload_to in migrations.test_writer'):
            self.serialize_round_trip(TestModel2.thing)

    def test_simple_migration(self):
        """
        Tests serializing a simple migration.
        """
        fields = {
            'charfield': models.DateTimeField(default=datetime.datetime.utcnow),
            'datetimefield': models.DateTimeField(default=datetime.datetime.utcnow),
        }

        options = {
            'verbose_name': 'My model',
            'verbose_name_plural': 'My models',
        }

        migration = type(str("Migration"), (migrations.Migration,), {
            "operations": [
                migrations.CreateModel("MyModel", tuple(fields.items()), options, (models.Model,)),
                migrations.CreateModel("MyModel2", tuple(fields.items()), bases=(models.Model,)),
                migrations.CreateModel(name="MyModel3", fields=tuple(fields.items()), options=options, bases=(models.Model,)),
                migrations.DeleteModel("MyModel"),
                migrations.AddField("OtherModel", "datetimefield", fields["datetimefield"]),
            ],
            "dependencies": [("testapp", "some_other_one")],
        })
        writer = MigrationWriter(migration)
        output = writer.as_string()
        # It should NOT be unicode.
        self.assertIsInstance(output, six.binary_type, "Migration as_string returned unicode")
        # We don't test the output formatting - that's too fragile.
        # Just make sure it runs for now, and that things look alright.
        result = self.safe_exec(output)
        self.assertIn("Migration", result)
        # In order to preserve compatibility with Python 3.2 unicode literals
        # prefix shouldn't be added to strings.
        tokens = tokenize.generate_tokens(six.StringIO(str(output)).readline)
        for token_type, token_source, (srow, scol), __, line in tokens:
            if token_type == tokenize.STRING:
                self.assertFalse(
                    token_source.startswith('u'),
                    "Unicode literal prefix found at %d:%d: %r" % (
                        srow, scol, line.strip()
                    )
                )

    def test_migration_path(self):
        test_apps = [
            'migrations.migrations_test_apps.normal',
            'migrations.migrations_test_apps.with_package_model',
            'migrations.migrations_test_apps.without_init_file',
        ]

        base_dir = os.path.dirname(os.path.dirname(__file__))

        for app in test_apps:
            with self.modify_settings(INSTALLED_APPS={'append': app}):
                migration = migrations.Migration('0001_initial', app.split('.')[-1])
                expected_path = os.path.join(base_dir, *(app.split('.') + ['migrations', '0001_initial.py']))
                writer = MigrationWriter(migration)
                # Silence warning on Python 2: Not importing directory
                # 'tests/migrations/migrations_test_apps/without_init_file/migrations':
                # missing __init__.py
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=ImportWarning)
                    self.assertEqual(writer.path, expected_path)

    def test_custom_operation(self):
        migration = type(str("Migration"), (migrations.Migration,), {
            "operations": [
                custom_migration_operations.operations.TestOperation(),
                custom_migration_operations.operations.CreateModel(),
                migrations.CreateModel("MyModel", (), {}, (models.Model,)),
                custom_migration_operations.more_operations.TestOperation()
            ],
            "dependencies": []
        })
        writer = MigrationWriter(migration)
        output = writer.as_string()
        result = self.safe_exec(output)
        self.assertIn("custom_migration_operations", result)
        self.assertNotEqual(
            result['custom_migration_operations'].operations.TestOperation,
            result['custom_migration_operations'].more_operations.TestOperation
        )

    def test_serialize_datetime(self):
        """
        #23365 -- Timezone-aware datetimes should be allowed.
        """
        # naive datetime
        naive_datetime = datetime.datetime(2014, 1, 1, 1, 1)
        self.assertEqual(MigrationWriter.serialize_datetime(naive_datetime),
                         "datetime.datetime(2014, 1, 1, 1, 1)")

        # datetime with utc timezone
        utc_datetime = datetime.datetime(2014, 1, 1, 1, 1, tzinfo=utc)
        self.assertEqual(MigrationWriter.serialize_datetime(utc_datetime),
                         "datetime.datetime(2014, 1, 1, 1, 1, tzinfo=utc)")

        # datetime with FixedOffset tzinfo
        fixed_offset_datetime = datetime.datetime(2014, 1, 1, 1, 1, tzinfo=FixedOffset(180))
        self.assertEqual(MigrationWriter.serialize_datetime(fixed_offset_datetime),
                         "datetime.datetime(2013, 12, 31, 22, 1, tzinfo=utc)")
