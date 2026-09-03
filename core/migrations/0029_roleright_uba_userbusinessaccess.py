import datetime

import django.db.models.deletion
import simple_history.models
from django.conf import settings
from django.db import migrations, models

import core.utils


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('core', '0028_alter_moduleconfiguration_module'),
    ]

    operations = [
        migrations.AddField(
            model_name='roleright',
            name='uba',
            field=models.BooleanField(
                db_column='UBA',
                default=False,
                help_text='False: the right is in the global bag, it is granted everywhere. True: the right is '
                          'granted only on the instances the user has a UserBusinessAccess link on, it never '
                          'appears in the global bag.',
            ),
        ),
        migrations.AddConstraint(
            model_name='roleright',
            constraint=models.UniqueConstraint(
                condition=models.Q(('validity_to__isnull', True)),
                fields=('role', 'right_id', 'uba'),
                name='rolerigh_unique_valid_role_right_uba',
            ),
        ),
        migrations.CreateModel(
            name='UserBusinessAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, editable=False, primary_key=True, serialize=False)),
                ('version', models.IntegerField(default=1)),
                ('uuid', models.UUIDField(db_column='UUID', default=core.utils.uuidv7, editable=False, unique=True)),
                ('active', models.BooleanField(default=True)),
                ('json_ext', models.JSONField(blank=True, db_column='Json_ext', null=True)),
                ('date_deactivated', models.DateTimeField(default=None, null=True)),
                ('date_valid_from', models.DateTimeField(db_column='DateValidFrom', default=datetime.datetime.now)),
                ('date_valid_to', models.DateTimeField(blank=True, db_column='DateValidTo', null=True)),
                ('replacement_uuid', models.UUIDField(blank=True, db_column='ReplacementUUID', null=True)),
                ('object_id', models.CharField(blank=True, max_length=36, null=True)),
                ('content_type', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    to='contenttypes.contenttype',
                )),
                ('link_type', models.CharField(
                    db_index=True,
                    help_text='The credential the user acts under on that object, a code registered through core.uba_link_types (not an openIMIS role). The same user may hold several credentials on one object, one row each.',
                    max_length=64,
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='business_accesses',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'User Business Access',
                'verbose_name_plural': 'User Business Accesses',
                'permissions': [
                    ('activate_userbusinessaccess', 'Can activate a user business access'),
                    ('deactivate_userbusinessaccess', 'Can deactivate a user business access'),
                ],
            },
        ),
        migrations.CreateModel(
            name='HistoricalUserBusinessAccess',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, editable=False)),
                ('version', models.IntegerField(default=1)),
                ('uuid', models.UUIDField(db_column='UUID', db_index=True, default=core.utils.uuidv7, editable=False)),
                ('active', models.BooleanField(default=True)),
                ('json_ext', models.JSONField(blank=True, db_column='Json_ext', null=True)),
                ('date_deactivated', models.DateTimeField(default=None, null=True)),
                ('date_valid_from', models.DateTimeField(db_column='DateValidFrom', default=datetime.datetime.now)),
                ('date_valid_to', models.DateTimeField(blank=True, db_column='DateValidTo', null=True)),
                ('replacement_uuid', models.UUIDField(blank=True, db_column='ReplacementUUID', null=True)),
                ('object_id', models.CharField(blank=True, max_length=36, null=True)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(
                    choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('content_type', models.ForeignKey(
                    blank=True,
                    db_constraint=False,
                    null=True,
                    on_delete=django.db.models.deletion.DO_NOTHING,
                    related_name='+',
                    to='contenttypes.contenttype',
                )),
                ('history_user', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('link_type', models.CharField(
                    db_index=True,
                    help_text='The credential the user acts under on that object, a code registered through core.uba_link_types (not an openIMIS role). The same user may hold several credentials on one object, one row each.',
                    max_length=64,
                )),
                ('user', models.ForeignKey(
                    blank=True,
                    db_constraint=False,
                    null=True,
                    on_delete=django.db.models.deletion.DO_NOTHING,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'historical User Business Access',
                'verbose_name_plural': 'historical User Business Accesses',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.AddIndex(
            model_name='userbusinessaccess',
            index=models.Index(fields=['content_type', 'object_id'], name='core_userbu_content_9f30b6_idx'),
        ),
        migrations.AddIndex(
            model_name='userbusinessaccess',
            index=models.Index(fields=['user', 'content_type', 'object_id'], name='core_userbu_user_id_628c07_idx'),
        ),
        migrations.AddIndex(
            model_name='userbusinessaccess',
            index=models.Index(fields=['user', 'link_type'], name='core_userbu_user_id_57d1ee_idx'),
        ),
        migrations.AddIndex(
            model_name='userbusinessaccess',
            index=models.Index(fields=['link_type', 'object_id'], name='core_userbu_link_ty_196d04_idx'),
        ),
    ]
