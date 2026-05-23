from django.db import models

# 강의 기본 정보(코드, 이름, 학점 등등)
class Course(models.Model):
    CATEGORY_CHOICES = [
        ('전공필수', '전공필수'),
        ('전공선택', '전공선택'),
        ('교양필수', '교양필수'),
        ('교양선택', '교양선택'),
    ]

    # 학기 개설 값 매핑 (spec 5.3.2, #25)
    #   1 = 1학기 / 2 = 2학기 / 3 = 하계 계절학기 / 4 = 동계 계절학기
    SEMESTER_OPEN_CHOICES = [
        (1, '1학기'),
        (2, '2학기'),
        (3, '하계 계절학기'),
        (4, '동계 계절학기'),
    ]

    course_code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    college = models.CharField(max_length=50)
    department = models.CharField(max_length=50, null=True, blank=True)
    major = models.CharField(max_length=50, null=True, blank=True)
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES)
    credits = models.IntegerField()
    year_open = models.IntegerField()
    semester_open = models.IntegerField(choices=SEMESTER_OPEN_CHOICES)
    professor = models.CharField(max_length=50, blank=True, default='')
    tags = models.JSONField(default=list, blank=True)  # 관심사 매칭용 태그 (옵션 A, spec 5.3.1)

    class Meta:
        db_table = 'courses_course'
        ordering = ['course_code']

    def __str__(self):
        return f"[{self.course_code}] {self.name}"


# 학기·분반별 개설 정보 (#36)
# 한 Course에 학기/분반별 여러 Offering. 분반은 강좌번호(section_no)가 유일 식별자.
# Schedule은 분반 단위 시간/강의실이라 Offering에 1:N으로 붙음.
class CourseOffering(models.Model):
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name='offerings'
    )
    year = models.IntegerField()  # 개설 연도 (예: 2026)
    semester = models.IntegerField(choices=Course.SEMESTER_OPEN_CHOICES)  # 1/2/3/4 (Course와 동일 매핑)
    section_no = models.CharField(max_length=10)  # 강좌번호 — 학기 안 유일 식별자 (예: '0729')
    professor = models.CharField(max_length=50, blank=True, default='')
    capacity = models.IntegerField(null=True, blank=True)  # 제한인원
    note = models.CharField(max_length=200, blank=True, default='')  # 비고 (예: 'IPP 우선수강')

    class Meta:
        db_table = 'courses_courseoffering'
        # 강좌번호는 학기 안에서 유일 (다른 학기엔 같은 번호 재사용 가능)
        unique_together = ('year', 'semester', 'section_no')
        ordering = ['year', 'semester', 'section_no']

    def __str__(self):
        return f"[{self.section_no}] {self.course.name} ({self.year}-{self.semester})"


# 선수 과목 관계 저장
class CoursePrerequisite(models.Model):
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name='prerequisites'
    )
    prerequisite = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name='required_by'
    )
    # prerequisite ← course 관계: course를 듣기 위해 prerequisite가 필요

    class Meta:
        db_table = 'courses_courseprerequisite'
        unique_together = ('course', 'prerequisite')

    def __str__(self):
        return f"{self.course.name} <- {self.prerequisite.name}"


# 강의 정보 : 요일/시간/강의실
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
    # 분반별 시간/강의실은 offering에 붙음. 기존 시드/테스트 호환을 위해 course FK도 유지 (#36)
    # 새 import는 두 FK 동시 채움 (offering.course == course). 기존 시드는 offering=NULL.
    offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.CASCADE,
        related_name='schedules',
        null=True,
        blank=True,
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


# 졸업 필요 학점
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


# 학사 일정 : 수강신청 기간, 학기 시작/종료일
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
