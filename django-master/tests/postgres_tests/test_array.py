import json
import unittest

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.forms import SimpleArrayField, SplitArrayField
from django.core import exceptions, serializers
from django.core.management import call_command
from django.db import models, IntegrityError, connection
from django.db.migrations.writer import MigrationWriter
from django import forms
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import IntegerArrayModel, NullableIntegerArrayModel, CharArrayModel, DateTimeArrayModel, NestedIntegerArrayModel


@unittest.skipUnless(connection.vendor == 'postgresql', 'PostgreSQL required')
class TestSaveLoad(TestCase):

    def test_integer(self):
        instance = IntegerArrayModel(field=[1, 2, 3])
        instance.save()
        loaded = IntegerArrayModel.objects.get()
        self.assertEqual(instance.field, loaded.field)

    def test_char(self):
        instance = CharArrayModel(field=['hello', 'goodbye'])
        instance.save()
        loaded = CharArrayModel.objects.get()
        self.assertEqual(instance.field, loaded.field)

    def test_dates(self):
        instance = DateTimeArrayModel(field=[timezone.now()])
        instance.save()
        loaded = DateTimeArrayModel.objects.get()
        self.assertEqual(instance.field, loaded.field)

    def test_tuples(self):
        instance = IntegerArrayModel(field=(1,))
        instance.save()
        loaded = IntegerArrayModel.objects.get()
        self.assertSequenceEqual(instance.field, loaded.field)

    def test_integers_passed_as_strings(self):
        # This checks that get_prep_value is deferred properly
        instance = IntegerArrayModel(field=['1'])
        instance.save()
        loaded = IntegerArrayModel.objects.get()
        self.assertEqual(loaded.field, [1])

    def test_null_handling(self):
        instance = NullableIntegerArrayModel(field=None)
        instance.save()
        loaded = NullableIntegerArrayModel.objects.get()
        self.assertEqual(instance.field, loaded.field)

        instance = IntegerArrayModel(field=None)
        with self.assertRaises(IntegrityError):
            instance.save()

    def test_nested(self):
        instance = NestedIntegerArrayModel(field=[[1, 2], [3, 4]])
        instance.save()
        loaded = NestedIntegerArrayModel.objects.get()
        self.assertEqual(instance.field, loaded.field)


@unittest.skipUnless(connection.vendor == 'postgresql', 'PostgreSQL required')
class TestQuerying(TestCase):

    def setUp(self):
        self.objs = [
            NullableIntegerArrayModel.objects.create(field=[1]),
            NullableIntegerArrayModel.objects.create(field=[2]),
            NullableIntegerArrayModel.objects.create(field=[2, 3]),
            NullableIntegerArrayModel.objects.create(field=[20, 30, 40]),
            NullableIntegerArrayModel.objects.create(field=None),
        ]

    def test_exact(self):
        self.assertSequenceEqual(
            NullableIntegerArrayModel.objects.filter(field__exact=[1]),
            self.objs[:1]
        )

    def test_isnull(self):
        self.assertSequenceEqual(
            NullableIntegerArrayModel.objects.filter(field__isnull=True),
            self.objs[-1:]
        )

    def test_gt(self):
        self.assertSequenceEqual(
            NullableIntegerArrayModel.objects.filter(field__gt=[0]),
            self.objs[:4]
        )

    def test_lt(self):
        self.assertSequenceEqual(
            NullableIntegerArrayModel.objects.filter(field__lt=[2]),
            self.objs[:1]
        )

    def test_in(self):
        self.assertSequenceEqual(
            NullableIntegerArrayModel.objects.filter(field__in=[[1], [2]]),
            self.objs[:2]
        )

    def test_contained_by(self):
        self.assertSequenceEqual(
            NullableIntegerArrayModel.objects.filter(field__contained_by=[1, 2]),
            self.objs[:2]
        )

    def test_contains(self):
        self.assertSequenceEqual(
            NullableIntegerArrayModel.objects.filter(field__contains=[2]),
            self.objs[1:3]
        )

    def test_contains_charfield(self):
        # Regression for #22907
        self.assertSequenceEqual(
            CharArrayModel.objects.filter(field__contains=['text']),
            []
        )

    def test_index(self):
        self.assertSequenceEqual(
            NullableIntegerArrayModel.objects.filter(field__0=2),
            self.objs[1:3]
        )

    def test_index_chained(self):
        self.assertSequenceEqual(
            NullableIntegerArrayModel.objects.filter(field__0__lt=3),
            self.objs[0:3]
        )

    def test_index_nested(self):
        instance = NestedIntegerArrayModel.objects.create(field=[[1, 2], [3, 4]])
        self.assertSequenceEqual(
            NestedIntegerArrayModel.objects.filter(field__0__0=1),
            [instance]
        )

    @unittest.expectedFailure
    def test_index_used_on_nested_data(self):
        instance = NestedIntegerArrayModel.objects.create(field=[[1, 2], [3, 4]])
        self.assertSequenceEqual(
            NestedIntegerArrayModel.objects.filter(field__0=[1, 2]),
            [instance]
        )

    def test_overlap(self):
        self.assertSequenceEqual(
            NullableIntegerArrayModel.objects.filter(field__overlap=[1, 2]),
            self.objs[0:3]
        )

    def test_len(self):
        self.assertSequenceEqual(
            NullableIntegerArrayModel.objects.filter(field__len__lte=2),
            self.objs[0:3]
        )

    def test_slice(self):
        self.assertSequenceEqual(
            NullableIntegerArrayModel.objects.filter(field__0_1=[2]),
            self.objs[1:3]
        )

        self.assertSequenceEqual(
            NullableIntegerArrayModel.objects.filter(field__0_2=[2, 3]),
            self.objs[2:3]
        )

    @unittest.expectedFailure
    def test_slice_nested(self):
        instance = NestedIntegerArrayModel.objects.create(field=[[1, 2], [3, 4]])
        self.assertSequenceEqual(
            NestedIntegerArrayModel.objects.filter(field__0__0_1=[1]),
            [instance]
        )


class TestChecks(TestCase):

    def test_field_checks(self):
        field = ArrayField(models.CharField())
        field.set_attributes_from_name('field')
        errors = field.check()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, 'postgres.E001')

    def test_invalid_base_fields(self):
        field = ArrayField(models.ManyToManyField('postgres_tests.IntegerArrayModel'))
        field.set_attributes_from_name('field')
        errors = field.check()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, 'postgres.E002')


class TestMigrations(TestCase):

    def test_deconstruct(self):
        field = ArrayField(models.IntegerField())
        name, path, args, kwargs = field.deconstruct()
        new = ArrayField(*args, **kwargs)
        self.assertEqual(type(new.base_field), type(field.base_field))

    def test_deconstruct_with_size(self):
        field = ArrayField(models.IntegerField(), size=3)
        name, path, args, kwargs = field.deconstruct()
        new = ArrayField(*args, **kwargs)
        self.assertEqual(new.size, field.size)

    def test_deconstruct_args(self):
        field = ArrayField(models.CharField(max_length=20))
        name, path, args, kwargs = field.deconstruct()
        new = ArrayField(*args, **kwargs)
        self.assertEqual(new.base_field.max_length, field.base_field.max_length)

    def test_makemigrations(self):
        field = ArrayField(models.CharField(max_length=20))
        statement, imports = MigrationWriter.serialize(field)
        self.assertEqual(statement, 'django.contrib.postgres.fields.ArrayField(models.CharField(max_length=20), size=None)')

    @override_settings(MIGRATION_MODULES={
        "postgres_tests": "postgres_tests.array_default_migrations",
    })
    def test_adding_field_with_default(self):
        # See #22962
        call_command('migrate', 'postgres_tests', verbosity=0)


@unittest.skipUnless(connection.vendor == 'postgresql', 'PostgreSQL required')
class TestSerialization(TestCase):
    test_data = '[{"fields": {"field": "[\\"1\\", \\"2\\"]"}, "model": "postgres_tests.integerarraymodel", "pk": null}]'

    def test_dumping(self):
        instance = IntegerArrayModel(field=[1, 2])
        data = serializers.serialize('json', [instance])
        self.assertEqual(json.loads(data), json.loads(self.test_data))

    def test_loading(self):
        instance = list(serializers.deserialize('json', self.test_data))[0].object
        self.assertEqual(instance.field, [1, 2])


class TestValidation(TestCase):

    def test_unbounded(self):
        field = ArrayField(models.IntegerField())
        with self.assertRaises(exceptions.ValidationError) as cm:
            field.clean([1, None], None)
        self.assertEqual(cm.exception.code, 'item_invalid')
        self.assertEqual(cm.exception.message % cm.exception.params, 'Item 1 in the array did not validate: This field cannot be null.')

    def test_blank_true(self):
        field = ArrayField(models.IntegerField(blank=True, null=True))
        # This should not raise a validation error
        field.clean([1, None], None)

    def test_with_size(self):
        field = ArrayField(models.IntegerField(), size=3)
        field.clean([1, 2, 3], None)
        with self.assertRaises(exceptions.ValidationError) as cm:
            field.clean([1, 2, 3, 4], None)
        self.assertEqual(cm.exception.messages[0], 'List contains 4 items, it should contain no more than 3.')

    def test_nested_array_mismatch(self):
        field = ArrayField(ArrayField(models.IntegerField()))
        field.clean([[1, 2], [3, 4]], None)
        with self.assertRaises(exceptions.ValidationError) as cm:
            field.clean([[1, 2], [3, 4, 5]], None)
        self.assertEqual(cm.exception.code, 'nested_array_mismatch')
        self.assertEqual(cm.exception.messages[0], 'Nested arrays must have the same length.')


class TestSimpleFormField(TestCase):

    def test_valid(self):
        field = SimpleArrayField(forms.CharField())
        value = field.clean('a,b,c')
        self.assertEqual(value, ['a', 'b', 'c'])

    def test_to_python_fail(self):
        field = SimpleArrayField(forms.IntegerField())
        with self.assertRaises(exceptions.ValidationError) as cm:
            field.clean('a,b,9')
        self.assertEqual(cm.exception.messages[0], 'Item 0 in the array did not validate: Enter a whole number.')

    def test_validate_fail(self):
        field = SimpleArrayField(forms.CharField(required=True))
        with self.assertRaises(exceptions.ValidationError) as cm:
            field.clean('a,b,')
        self.assertEqual(cm.exception.messages[0], 'Item 2 in the array did not validate: This field is required.')

    def test_validators_fail(self):
        field = SimpleArrayField(forms.RegexField('[a-e]{2}'))
        with self.assertRaises(exceptions.ValidationError) as cm:
            field.clean('a,bc,de')
        self.assertEqual(cm.exception.messages[0], 'Item 0 in the array did not validate: Enter a valid value.')

    def test_delimiter(self):
        field = SimpleArrayField(forms.CharField(), delimiter='|')
        value = field.clean('a|b|c')
        self.assertEqual(value, ['a', 'b', 'c'])

    def test_delimiter_with_nesting(self):
        field = SimpleArrayField(SimpleArrayField(forms.CharField()), delimiter='|')
        value = field.clean('a,b|c,d')
        self.assertEqual(value, [['a', 'b'], ['c', 'd']])

    def test_prepare_value(self):
        field = SimpleArrayField(forms.CharField())
        value = field.prepare_value(['a', 'b', 'c'])
        self.assertEqual(value, 'a,b,c')

    def test_max_length(self):
        field = SimpleArrayField(forms.CharField(), max_length=2)
        with self.assertRaises(exceptions.ValidationError) as cm:
            field.clean('a,b,c')
        self.assertEqual(cm.exception.messages[0], 'List contains 3 items, it should contain no more than 2.')

    def test_min_length(self):
        field = SimpleArrayField(forms.CharField(), min_length=4)
        with self.assertRaises(exceptions.ValidationError) as cm:
            field.clean('a,b,c')
        self.assertEqual(cm.exception.messages[0], 'List contains 3 items, it should contain no fewer than 4.')

    def test_required(self):
        field = SimpleArrayField(forms.CharField(), required=True)
        with self.assertRaises(exceptions.ValidationError) as cm:
            field.clean('')
        self.assertEqual(cm.exception.messages[0], 'This field is required.')

    def test_model_field_formfield(self):
        model_field = ArrayField(models.CharField(max_length=27))
        form_field = model_field.formfield()
        self.assertIsInstance(form_field, SimpleArrayField)
        self.assertIsInstance(form_field.base_field, forms.CharField)
        self.assertEqual(form_field.base_field.max_length, 27)

    def test_model_field_formfield_size(self):
        model_field = ArrayField(models.CharField(max_length=27), size=4)
        form_field = model_field.formfield()
        self.assertIsInstance(form_field, SimpleArrayField)
        self.assertEqual(form_field.max_length, 4)


class TestSplitFormField(TestCase):

    def test_valid(self):
        class SplitForm(forms.Form):
            array = SplitArrayField(forms.CharField(), size=3)

        data = {'array_0': 'a', 'array_1': 'b', 'array_2': 'c'}
        form = SplitForm(data)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data, {'array': ['a', 'b', 'c']})

    def test_required(self):
        class SplitForm(forms.Form):
            array = SplitArrayField(forms.CharField(), required=True, size=3)

        data = {'array_0': '', 'array_1': '', 'array_2': ''}
        form = SplitForm(data)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {'array': ['This field is required.']})

    def test_remove_trailing_nulls(self):
        class SplitForm(forms.Form):
            array = SplitArrayField(forms.CharField(required=False), size=5, remove_trailing_nulls=True)

        data = {'array_0': 'a', 'array_1': '', 'array_2': 'b', 'array_3': '', 'array_4': ''}
        form = SplitForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data, {'array': ['a', '', 'b']})

    def test_required_field(self):
        class SplitForm(forms.Form):
            array = SplitArrayField(forms.CharField(), size=3)

        data = {'array_0': 'a', 'array_1': 'b', 'array_2': ''}
        form = SplitForm(data)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {'array': ['Item 2 in the array did not validate: This field is required.']})

    def test_rendering(self):
        class SplitForm(forms.Form):
            array = SplitArrayField(forms.CharField(), size=3)

        self.assertHTMLEqual(str(SplitForm()), '''
            <tr>
                <th><label for="id_array_0">Array:</label></th>
                <td>
                    <input id="id_array_0" name="array_0" type="text" />
                    <input id="id_array_1" name="array_1" type="text" />
                    <input id="id_array_2" name="array_2" type="text" />
                </td>
            </tr>
        ''')
