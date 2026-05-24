# Generated for #47 Phase 3 — Course.category 학칙 7분류로 펼침
# 기존 '교양필수'/'교양선택' 라벨 → 공통/핵심/학문기초/일반교양으로 분해 (liberal_subtype 기준)

from django.db import migrations, models


NEW_CHOICES = [
    ('전공필수', '전공필수'),
    ('전공선택', '전공선택'),
    ('공통교양', '공통교양'),
    ('핵심교양', '핵심교양'),
    ('학문기초교양', '학문기초교양'),
    ('일반교양', '일반교양'),
    ('자유선택', '자유선택'),
]


def forwards(apps, schema_editor):
    """기존 row category 갱신:
    - '교양필수' → '공통교양' (학칙 §10: 교필=공통교양)
    - '교양선택' + liberal_subtype → liberal_subtype을 category로
    - 잔여 '교양선택' (liberal_subtype null) → '일반교양' (안전 fallback)
    """
    Course = apps.get_model('courses', 'Course')
    GR = apps.get_model('courses', 'GraduationRequirement')
    for model in [Course, GR]:
        model.objects.filter(category='교양필수').update(category='공통교양')
        for sub in ['공통교양', '핵심교양', '학문기초교양', '일반교양']:
            model.objects.filter(category='교양선택', liberal_subtype=sub).update(category=sub)
        model.objects.filter(category='교양선택').update(category='일반교양')


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0007_alter_graduationrequirement_unique_together_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='course',
            name='category',
            field=models.CharField(max_length=10, choices=NEW_CHOICES),
        ),
        migrations.AlterField(
            model_name='graduationrequirement',
            name='category',
            field=models.CharField(max_length=10, choices=NEW_CHOICES),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
