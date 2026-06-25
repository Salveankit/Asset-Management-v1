from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("licences", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="license",
            name="billing_term",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="license",
            name="reference_code",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="license",
            name="renewal_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
