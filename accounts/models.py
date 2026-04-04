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
    major = models.CharField(max_length=100, blank=True, verbose_name='전공')
    grade = models.IntegerField(null=True, blank=True, verbose_name='학년')
    semester = models.IntegerField(null=True, blank=True, verbose_name='학기')
    graduation_year = models.IntegerField(null=True, blank=True, verbose_name='졸업 희망 연도')
    is_email_verified = models.BooleanField(default=False, verbose_name='이메일 인증 여부')
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
    credits = models.IntegerField(verbose_name='학점 수')

    class Meta:
        verbose_name = '수강이력'
        verbose_name_plural = '수강이력'

    def __str__(self):
        return f'{self.user.email} - {self.course_name}'


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
