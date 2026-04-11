from django.db import models


class Course(models.Model):
    CATEGORY_CHOICES = [
        ('전공필수', '전공필수'),
        ('전공선택', '전공선택'),
        ('교양필수', '교양필수'),
        ('교양선택', '교양선택'),
    ]

    course_code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    college = models.CharField(max_length=50)
    department = models.CharField(max_length=50, null=True, blank=True)
    major = models.CharField(max_length=50, null=True, blank=True)
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES)
    credits = models.IntegerField()
    year_open = models.IntegerField()
    semester_open = models.IntegerField()
    professor = models.CharField(max_length=50, blank=True, default='')

    class Meta:
        db_table = 'courses_course'
        ordering = ['course_code']

    def __str__(self):
        return f"[{self.course_code}] {self.name}"


class CoursePrerequisite(models.Model):
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name='prerequisites'
    )
    prerequisite = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name='required_by'
    )

    class Meta:
        db_table = 'courses_courseprerequisite'
        unique_together = ('course', 'prerequisite')

    def __str__(self):
        return f"{self.course.name} <- {self.prerequisite.name}"


class CourseSchedule(models.Model):
    DAY_CHOICES = [
        ('월', '월'),
        ('화', '화'),
        ('수', '수'),
        ('목', '목'),
        ('금', '금'),
    ]

    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name='schedules'
    )
    day_of_week = models.CharField(max_length=2, choices=DAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()
    building = models.CharField(max_length=50, blank=True, default='')
    room = models.CharField(max_length=20, blank=True, default='')

    class Meta:
        db_table = 'courses_courseschedule'

    def __str__(self):
        return f"{self.course.name} {self.day_of_week} {self.start_time}-{self.end_time}"


class GraduationRequirement(models.Model):
    CATEGORY_CHOICES = [
        ('전공필수', '전공필수'),
        ('전공선택', '전공선택'),
        ('교양필수', '교양필수'),
        ('교양선택', '교양선택'),
    ]

    department = models.CharField(max_length=50)
    admission_year = models.IntegerField()
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES)
    required_credits = models.IntegerField()
    total_required = models.IntegerField()

    class Meta:
        db_table = 'courses_graduationrequirement'
        unique_together = ('department', 'admission_year', 'category')

    def __str__(self):
        return f"{self.department} {self.admission_year} {self.category}: {self.required_credits}학점"


class AcademicCalendar(models.Model):
    year = models.IntegerField()
    semester = models.IntegerField()
    pre_registration_start = models.DateField(null=True, blank=True)
    pre_registration_end = models.DateField(null=True, blank=True)
    registration_start = models.DateField(null=True, blank=True)
    registration_end = models.DateField(null=True, blank=True)
    adjustment_start = models.DateField(null=True, blank=True)
    adjustment_end = models.DateField(null=True, blank=True)
    semester_start = models.DateField(null=True, blank=True)
    semester_end = models.DateField(null=True, blank=True)

    class Meta:
        db_table = 'courses_academiccalendar'
        unique_together = ('year', 'semester')

    def __str__(self):
        return f"{self.year}년 {self.semester}학기"
