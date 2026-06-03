from django.db import models
from django.utils.functional import cached_property


# 시간표 추천(#97 5.3.6) 충돌 검사용 bitmap 상수.
# 5요일 × 26 slot(09:00~22:00, 30분 단위). day별 32비트 분리(26+여유 6)로 비트 정렬 깔끔.
TIMETABLE_DAY_IDX = {'월': 0, '화': 1, '수': 2, '목': 3, '금': 4}
TIMETABLE_SLOT_BITS_PER_DAY = 32
TIMETABLE_SLOTS = 26  # 09:00 시작 26 slot → 22:00


def _slot_floor(t) -> int:
    """시간 t를 09:00 기준 30분 슬롯 인덱스로 floor (start_time용)."""
    return (t.hour - 9) * 2 + (1 if t.minute >= 30 else 0)


def _slot_ceil(t) -> int:
    """시간 t를 09:00 기준 30분 슬롯 인덱스로 ceil (end_time용, exclusive).

    end_time이 슬롯 경계와 안 맞으면 한 슬롯 더 차지(보수적). 예: 11:50 → slot 6 (11:30~12:00 점유).
    """
    base = (t.hour - 9) * 2
    if t.minute > 30:
        return base + 2
    if t.minute > 0:
        return base + 1
    return base


# 강의 기본 정보(코드, 이름, 학점 등등)
class Course(models.Model):
    # graduation_requirements.md §1·§3·§4 학칙 7분류로 펼침 (#47 Phase 3).
    # 교양 4종을 category 1단으로 직접 표현 — 기존 '교양필수'/'교양선택'은 학칙 행정 분류와 불일치라 폐기.
    # liberal_subtype 필드는 호환용으로 유지(category와 동기화).
    CATEGORY_CHOICES = [
        ('전공필수', '전공필수'),
        ('전공선택', '전공선택'),
        ('공통교양', '공통교양'),
        ('핵심교양', '핵심교양'),
        ('학문기초교양', '학문기초교양'),
        ('일반교양', '일반교양'),
        ('자유선택', '자유선택'),
    ]

    # 교양 4종 (학칙 별표2·2-1 + graduation_requirements.md §3). 전공 과목은 null.
    # 학교 강의시간표 엑셀에는 이 분류가 없어서 import 시점에 매핑 룰로 채운다 (courses/category_map.py).
    LIBERAL_SUBTYPE_CHOICES = [
        ('공통교양', '공통교양'),
        ('핵심교양', '핵심교양'),
        ('학문기초교양', '학문기초교양'),
        ('일반교양', '일반교양'),
    ]

    # 교양 영역 — core_area 필드에 박힘 (이름은 핵심교양 도입 시 정해진 것이나 의미는 일반 교양 영역).
    # 핵심교양 4영역 (graduation_requirements.md §4.3, 영역당 1과목 택1):
    # 공통교양 4영역 (graduation_requirements.md §4.2, 영역별 학점 6/3/6/2 = 17학점):
    CORE_AREA_CHOICES = [
        # 핵심교양 (liberal_subtype='핵심교양' 행에만)
        ('역사와 철학', '역사와 철학'),
        ('사회와 공동체', '사회와 공동체'),
        ('문화와 예술', '문화와 예술'),
        ('과학기술과 정보', '과학기술과 정보'),
        # 공통교양 (liberal_subtype='공통교양' 행에만)
        ('기독교', '기독교'),
        ('사고와 표현', '사고와 표현'),
        ('언어', '언어'),
        ('진로와 디지털리터러시', '진로와 디지털리터러시'),
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
    # 교양 세부 분류 — category='교양선택'/'교양필수' 행에 한해 채움. 전공/군사 등은 null.
    liberal_subtype = models.CharField(
        max_length=10, choices=LIBERAL_SUBTYPE_CHOICES, null=True, blank=True
    )
    # 핵심교양 4영역 — liberal_subtype=='핵심교양' 행만 채움. 그 외 null.
    # (영역별 필수 1과목 충족 판정용. import 시점에 category_map.classify_core_area로 채운다.)
    core_area = models.CharField(
        max_length=20, choices=CORE_AREA_CHOICES, null=True, blank=True
    )
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
    # 사이버(원격)강의 — 정해진 시간이 없어 schedules가 비어 있고 시간표 충돌에서 자유롭다 (#222).
    # 학교 강의시간표는 시간 없는 원격수업을 00:00 placeholder로 표기 → import에서 schedule 대신 이 플래그로 처리.
    is_online = models.BooleanField(default=False)

    class Meta:
        db_table = 'courses_courseoffering'
        # 강좌번호는 학기 안에서 유일 (다른 학기엔 같은 번호 재사용 가능)
        unique_together = ('year', 'semester', 'section_no')
        ordering = ['year', 'semester', 'section_no']

    def __str__(self):
        return f"[{self.section_no}] {self.course.name} ({self.year}-{self.semester})"

    @cached_property
    def time_bitmap(self) -> int:
        """시간표 추천(#97 5.3.6) 충돌 검사용 5요일 × 26 slot bitmap.

        bit index: day_idx * TIMETABLE_SLOT_BITS_PER_DAY + slot_idx.
        충돌 검사: (a.time_bitmap & b.time_bitmap) != 0
        09:00 이전 / 22:00 이후 시간은 범위 clamp (실데이터에 거의 없음).
        토/일 또는 미지원 요일은 skip.
        """
        bits = 0
        for sch in self.schedules.all():
            day = TIMETABLE_DAY_IDX.get(sch.day_of_week)
            if day is None:
                continue
            start = max(_slot_floor(sch.start_time), 0)
            end = min(_slot_ceil(sch.end_time), TIMETABLE_SLOTS)
            for s in range(start, end):
                bits |= 1 << (day * TIMETABLE_SLOT_BITS_PER_DAY + s)
        return bits


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
    # Course와 동일한 7분류 사용 (#47 Phase 3)
    CATEGORY_CHOICES = Course.CATEGORY_CHOICES

    department = models.CharField(max_length=50)
    admission_year = models.IntegerField()
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES)
    # 교양 4종 분해용 (graduation_requirements.md §1·§3). 전공·자유선택 row는 null.
    # (department, admission_year, category, liberal_subtype, core_area) 조합이 유일해야 함.
    liberal_subtype = models.CharField(
        max_length=10, choices=Course.LIBERAL_SUBTYPE_CHOICES, null=True, blank=True
    )
    # 핵심교양 4영역 row 분해용 — liberal_subtype=='핵심교양'일 때만 채움 (graduation_requirements.md §4.3).
    # 영역당 3학점 4 row(역사·철학/사회·공동체/문화·예술/과학기술·정보)로 핵심교양 12학점을 쪼개 박는다.
    core_area = models.CharField(
        max_length=20, choices=Course.CORE_AREA_CHOICES, null=True, blank=True
    )
    required_credits = models.IntegerField()
    total_required = models.IntegerField()

    class Meta:
        db_table = 'courses_graduationrequirement'
        unique_together = ('department', 'admission_year', 'category', 'liberal_subtype', 'core_area')

    def __str__(self):
        sub = f"/{self.liberal_subtype}" if self.liberal_subtype else ''
        area = f"/{self.core_area}" if self.core_area else ''
        return f"{self.department} {self.admission_year} {self.category}{sub}{area}: {self.required_credits}학점"


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
