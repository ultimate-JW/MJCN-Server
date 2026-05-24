from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('이메일은 필수입니다.')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_email_verified', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True, verbose_name='이메일')
    name = models.CharField(max_length=50, blank=True, verbose_name='이름')
    grade = models.IntegerField(null=True, blank=True, verbose_name='학년')
    semester = models.IntegerField(null=True, blank=True, verbose_name='학기')
    graduation_year = models.IntegerField(null=True, blank=True, verbose_name='졸업 희망 연도')
    graduation_month = models.IntegerField(null=True, blank=True, verbose_name='졸업 희망 월')
    admission_year = models.IntegerField(null=True, blank=True, verbose_name='입학 연도')
    # 채플 누적 이수 회수 (graduation_requirements.md §2.1).
    # 학번별 요구: 1996~1998학번 2회 / 1999학번 이후 4회. 학점은 0.5×회수로 공통교양 합산.
    chapel_count = models.IntegerField(default=0, verbose_name='채플 누적 이수 회수')
    major = models.CharField(max_length=100, blank=True, verbose_name='전공')
    is_email_verified = models.BooleanField(default=False, verbose_name='이메일 인증 여부')
    is_onboarding_completed = models.BooleanField(default=False, verbose_name='온보딩 완료 여부')
    notification_enabled = models.BooleanField(default=True, verbose_name='전체 알림')
    notification_chat = models.BooleanField(default=True, verbose_name='AI 채팅 알림')
    notification_notice = models.BooleanField(default=True, verbose_name='공지 알림')
    notification_information = models.BooleanField(default=True, verbose_name='정보 알림')
    kakao_id = models.CharField(max_length=100, null=True, blank=True, unique=True, verbose_name='카카오 ID')

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = '사용자'
        verbose_name_plural = '사용자'

    def __str__(self):
        return self.email


INTEREST_CATEGORY_CHOICES = [
    ('IT/개발', 'IT/개발'),
    ('디자인', '디자인'),
    ('마케팅/광고', '마케팅/광고'),
    ('금융/회계', '금융/회계'),
    ('교육', '교육'),
    ('공기업/공공기관', '공기업/공공기관'),
    ('의료/바이오', '의료/바이오'),
    ('미디어/콘텐츠', '미디어/콘텐츠'),
    ('건축/공간', '건축/공간'),
    ('스포츠/예술', '스포츠/예술'),
    ('연구/R&D', '연구/R&D'),
    ('기타', '기타'),
]


class InterestArea(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='interests')
    category = models.CharField(max_length=30, choices=INTEREST_CATEGORY_CHOICES, verbose_name='관심분야')
    custom_text = models.TextField(blank=True, verbose_name='자유 텍스트')

    class Meta:
        verbose_name = '관심분야'
        verbose_name_plural = '관심분야'

    def __str__(self):
        return f'{self.user.email} - {self.category}'


class CourseHistory(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='course_histories')
    course_name = models.CharField(max_length=100, verbose_name='과목명')
    course_code = models.CharField(max_length=30, verbose_name='과목번호')
    year = models.IntegerField(verbose_name='수강 연도')
    semester = models.IntegerField(verbose_name='수강 학기')
    grade_received = models.CharField(max_length=10, blank=True, verbose_name='취득 성적')
    category = models.CharField(max_length=20, verbose_name='이수구분')
    # 교양 4종 (공통/핵심/학문기초/일반). 저장 시 course_code로 Course 찾아 자동 채움 (#47).
    # 전공·자유선택·매칭 안되는 교양은 null. 점수 계산의 4종별 진척도 키로 사용 (services._build_short_keys).
    liberal_subtype = models.CharField(
        max_length=10, null=True, blank=True, verbose_name='교양 4종 분류',
    )
    # 핵심교양 4영역 — liberal_subtype=='핵심교양' 행만 채움. save() 시 Course에서 자동 복사 (#47 Phase 2).
    # 영역별 부족분(영역당 3학점 4row) 판정 키로 사용.
    core_area = models.CharField(
        max_length=20, null=True, blank=True, verbose_name='핵심교양 영역',
    )
    credits = models.IntegerField(verbose_name='학점 수')

    class Meta:
        verbose_name = '수강이력'
        verbose_name_plural = '수강이력'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'course_code', 'year', 'semester'],
                name='unique_course_history_per_term',
            ),
        ]

    def __str__(self):
        return f'{self.user.email} - {self.course_name}'

    def save(self, *args, **kwargs):
        # liberal_subtype / core_area 자동 채움 — course_code로 Course 찾아 복사 (#47).
        # 명시적으로 값을 넘긴 호출자가 있으면 덮지 않음. bulk_create는 save() 우회하니 별도 보강 필요.
        need_subtype = not self.liberal_subtype
        need_area = not self.core_area
        if (need_subtype or need_area) and self.course_code:
            from courses.models import Course
            row = Course.objects.filter(course_code=self.course_code).values(
                'liberal_subtype', 'core_area',
            ).first()
            if row:
                if need_subtype and row['liberal_subtype']:
                    self.liberal_subtype = row['liberal_subtype']
                if need_area and row['core_area']:
                    self.core_area = row['core_area']
        super().save(*args, **kwargs)


class CurrentCourse(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='current_courses')
    course_name = models.CharField(max_length=100, verbose_name='과목명')
    course_code = models.CharField(max_length=30, verbose_name='과목번호')
    day_of_week = models.CharField(max_length=5, verbose_name='요일')
    start_time = models.TimeField(verbose_name='시작 시간')
    end_time = models.TimeField(verbose_name='종료 시간')
    professor = models.CharField(max_length=50, blank=True, verbose_name='교수명')
    room = models.CharField(max_length=30, blank=True, verbose_name='강의실')
    building = models.CharField(max_length=50, blank=True, verbose_name='강의실 위치')

    class Meta:
        verbose_name = '현재 수강과목'
        verbose_name_plural = '현재 수강과목'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'day_of_week', 'start_time'],
                name='unique_current_course_per_slot',
            ),
        ]

    def __str__(self):
        return f'{self.user.email} - {self.course_name}'


class EmailVerification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_verifications')
    code = models.CharField(max_length=8, verbose_name='인증 코드')
    purpose = models.CharField(max_length=20, default='signup', verbose_name='용도')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성 시각')
    expires_at = models.DateTimeField(verbose_name='만료 시각')
    is_used = models.BooleanField(default=False, verbose_name='사용 여부')

    class Meta:
        verbose_name = '이메일 인증'
        verbose_name_plural = '이메일 인증'

    def __str__(self):
        return f'{self.user.email} - {self.code}'


class Bookmark(models.Model):
    """사용자별 공지·정보 북마크 (spec 4.x / 6.11).

    content_type + object_id 조합으로 Notice 또는 Information을 참조.
    Generic ForeignKey 안 쓰고 단순 CharField+IntegerField (spec 일치 + 의존 최소화).
    """
    CONTENT_TYPE_NOTICE = 'notice'
    CONTENT_TYPE_INFORMATION = 'information'
    CONTENT_TYPE_CHOICES = [
        (CONTENT_TYPE_NOTICE, '공지'),
        (CONTENT_TYPE_INFORMATION, '정보'),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='bookmarks',
        verbose_name='사용자',
    )
    content_type = models.CharField(
        max_length=20, choices=CONTENT_TYPE_CHOICES, verbose_name='북마크 대상 종류',
    )
    object_id = models.IntegerField(verbose_name='대상 객체 ID')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='북마크 시각')

    class Meta:
        verbose_name = '북마크'
        verbose_name_plural = '북마크'
        # 같은 사용자가 같은 항목 중복 북마크 방지 (멱등 POST 안전망)
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'content_type', 'object_id'],
                name='unique_bookmark_per_user_target',
            ),
        ]
        # GET ?type=notice + 페이지네이션 ORDER BY created_at DESC 최적화
        indexes = [
            models.Index(fields=['user', 'content_type', '-created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.email} - {self.content_type}:{self.object_id}'
