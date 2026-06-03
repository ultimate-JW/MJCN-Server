from rest_framework import serializers

from .models import (
    AcademicCalendar,
    Course,
    CourseOffering,
    CoursePrerequisite,
    CourseSchedule,
    GraduationRequirement,
)


class CourseScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseSchedule
        # building은 xlsx 원천 데이터에 정보 없어 항상 빈 문자열로 노출되던 결함 H (#116).
        # 응답에서만 제외 — 모델 필드는 유지(학교가 공식 매핑 제공 시 다시 노출 예정).
        fields = ['day_of_week', 'start_time', 'end_time', 'room']


class CoursePrerequisiteSerializer(serializers.ModelSerializer):
    prerequisite_code = serializers.CharField(source='prerequisite.course_code', read_only=True)
    prerequisite_name = serializers.CharField(source='prerequisite.name', read_only=True)

    class Meta:
        model = CoursePrerequisite
        fields = ['prerequisite_code', 'prerequisite_name']


class CourseSerializer(serializers.ModelSerializer):
    schedules = CourseScheduleSerializer(many=True, read_only=True)
    prerequisites = CoursePrerequisiteSerializer(many=True, read_only=True)

    class Meta:
        model = Course
        fields = [
            'id', 'course_code', 'name', 'college', 'department', 'major',
            'category', 'credits', 'year_open', 'semester_open', 'professor',
            'schedules', 'prerequisites',
        ]


class CourseListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = [
            'id', 'course_code', 'name', 'college', 'department', 'major',
            'category', 'credits', 'professor',
        ]


class CourseOfferingListSerializer(serializers.ModelSerializer):
    """분반 단위 검색 응답 — 한 카드 = 한 offering (spec 6.6 `/courses/offerings/`, #149).

    `offering_id`(= `id`, offering PK) 값을 그대로 `POST /accounts/current-courses/`의
    `offering_id`로 전달하면 서버가 CurrentCourse의 7개 평문 필드(course_name·code·
    요일·시간·교수·강의실)를 자동 hydrate. `id`도 동일 값으로 함께 제공 (하위호환).
    """
    # current-courses POST의 offering_id로 그대로 전달할 수 있도록 명시 필드 추가 (#187)
    offering_id = serializers.IntegerField(source='id', read_only=True)
    course_code = serializers.CharField(source='course.course_code', read_only=True)
    name = serializers.CharField(source='course.name', read_only=True)
    college = serializers.CharField(source='course.college', read_only=True)
    department = serializers.CharField(source='course.department', read_only=True)
    major = serializers.CharField(source='course.major', read_only=True)
    category = serializers.CharField(source='course.category', read_only=True)
    credits = serializers.IntegerField(source='course.credits', read_only=True)
    schedules = CourseScheduleSerializer(many=True, read_only=True)

    class Meta:
        model = CourseOffering
        fields = [
            'id', 'offering_id', 'year', 'semester', 'section_no',
            'course_code', 'name', 'college', 'department', 'major',
            'category', 'credits', 'professor', 'schedules',
        ]


class GraduationRequirementSerializer(serializers.ModelSerializer):
    class Meta:
        model = GraduationRequirement
        fields = [
            'id', 'department', 'admission_year', 'category',
            'required_credits', 'total_required',
        ]


class AcademicCalendarSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicCalendar
        fields = [
            'id', 'year', 'semester',
            'pre_registration_start', 'pre_registration_end',
            'registration_start', 'registration_end',
            'adjustment_start', 'adjustment_end',
            'semester_start', 'semester_end',
        ]


# --- 추천/이수현황 응답 Serializer ---

class AreaCreditsSerializer(serializers.Serializer):
    """카테고리 안 영역별 학점 진척도 (공통교양 4영역 / 핵심교양 4영역).
    학칙 §4.2, §4.3 의무 — 영역별 충족 여부 노출용 (#68)."""
    area = serializers.CharField()
    completed = serializers.IntegerField()
    required = serializers.IntegerField()
    remaining = serializers.IntegerField()


class RequiredCourseStatusSerializer(serializers.Serializer):
    """카테고리 안 필수 과목 이수 여부 (전공필수 8과목 / 학문기초 5과목).
    학칙 §5.1, §4.4 의무 — 과목 단위 충족 여부 노출용 (#68)."""
    name = serializers.CharField()
    completed = serializers.BooleanField()


class CategoryCreditsSerializer(serializers.Serializer):
    category = serializers.CharField()
    completed = serializers.IntegerField()
    required = serializers.IntegerField()
    remaining = serializers.IntegerField()
    # 카테고리별 세부 분해 (#68). 카테고리별 적용 영역/과목이 없으면 None.
    areas = AreaCreditsSerializer(many=True, required=False, allow_null=True)
    required_courses = RequiredCourseStatusSerializer(many=True, required=False, allow_null=True)


class ChapelStatusSerializer(serializers.Serializer):
    """채플 이수 회수 진척도 (graduation_requirements.md §2.1).
    학번별 required: 1996~1998 = 2회 / 1999 이후 = 4회."""
    completed = serializers.IntegerField()
    required = serializers.IntegerField()
    remaining = serializers.IntegerField()


class CompletionStatusSerializer(serializers.Serializer):
    categories = CategoryCreditsSerializer(many=True)
    chapel = ChapelStatusSerializer()
    total_completed = serializers.IntegerField()
    total_required = serializers.IntegerField()
    total_remaining = serializers.IntegerField()


class RecommendedOfferingSerializer(serializers.Serializer):
    """분반 1개 — 추천 응답 안 schedules는 이 분반의 시간/강의실만 (#111).

    Course에 직접 붙은 schedules 평탄화를 피하고 분반(Offering) 단위로 그룹화.
    """
    id = serializers.IntegerField()
    section_no = serializers.CharField()
    professor = serializers.CharField()
    schedules = CourseScheduleSerializer(many=True)


class RecommendedCourseSerializer(serializers.Serializer):
    course_code = serializers.CharField()
    name = serializers.CharField()
    category = serializers.CharField()
    credits = serializers.IntegerField()
    offerings = RecommendedOfferingSerializer(many=True)
    # 핵심교양 4영역 / 공통교양 4영역 식별자 (#47 Phase 2, #113).
    # 비교양 카테고리는 null — 프론트가 영역별 진척도 매칭에 사용.
    core_area = serializers.CharField(allow_null=True)


class NextSemesterRecommendationSerializer(serializers.Serializer):
    """다음학기 추천 한 과목 응답 (spec 5.3.1).

    View에서 many=True로 호출되어 점수 내림차순 정렬된 리스트를 직렬화한다.
    `score`는 디버깅·튜닝 시 우선순위 검증용으로 함께 노출한다.
    offerings는 target_year/target_semester 매칭 분반만 (#111).
    """
    score = serializers.IntegerField()
    course_code = serializers.CharField()
    name = serializers.CharField()
    category = serializers.CharField()
    credits = serializers.IntegerField()
    offerings = RecommendedOfferingSerializer(many=True)
    # 핵심교양 4영역 / 공통교양 4영역 식별자 (#47 Phase 2, #113).
    core_area = serializers.CharField(allow_null=True)


class AdviceSerializer(serializers.Serializer):
    """② 띵똥이의 조언 (이슈 #164, Figma 160-1144).

    user_insight : 추천 결과 신호 기반 근거 멘트 (사용자별)
    stage_message: (학년, 학기) 고정 행동 멘트
    text         : 'OOO님, {user_insight} {stage_message}' 합본 — 프론트가 그대로 써도 됨
    """
    user_insight = serializers.CharField()
    stage_message = serializers.CharField()
    # 다음학기 데이터 미공개 시 작년 학기 기반 안내 (fallback 아니면 빈 문자열) (#164 발견1)
    term_note = serializers.CharField(allow_blank=True)
    text = serializers.CharField()


class QuickQuestionSerializer(serializers.Serializer):
    """④ 띵똥이에게 물어보기 칩 (이슈 #164).

    label : 버튼에 보이는 짧은 문구
    prompt: 탭하면 챗(POST /chat-rooms/{id}/messages/)으로 전송될 질문 문장
    """
    label = serializers.CharField()
    prompt = serializers.CharField()


class RecommendationSectionsSerializer(serializers.Serializer):
    """수강신청 테마 상세 ②조언 + ③2섹션 추천 + ④물어보기 응답 (spec 5.3.1 확장, #164, Figma 160-1144).

    같은 추천 엔진 풀을 두 우선순위로 재구성한 결과.
    카드는 RecommendedCourseSerializer 재사용 (course_code·name·category·credits·offerings·core_area).
    """
    target_year = serializers.IntegerField()
    target_semester = serializers.IntegerField()
    advice = AdviceSerializer()  # ② 띵똥이의 조언
    # ③-a 관심분야 추천 — custom_text 키워드 ↔ course.name 매칭
    interest_courses = RecommendedCourseSerializer(many=True)
    # ③-b 지난학기 맞춤형 — 직전학기 후수과목 우선 → 추천 점수순
    linked_courses = RecommendedCourseSerializer(many=True)
    quick_questions = QuickQuestionSerializer(many=True)  # ④ 띵똥이에게 물어보기


# ────────────────────────────────────────
# 취업·진로 로드맵 테마 상세 (이슈 #171, Figma 221-5848)
# ────────────────────────────────────────

class CareerAdviceSerializer(serializers.Serializer):
    """① 띵똥이의 조언 — 직무·전공기초 grounded 판정 기반 1문장 (#171).

    text: '{name}님은 현재 {job}를 목표로… {전공기초 판정}… 실무 준비 단계예요😊'.
    구조는 #164 AdviceSerializer와 분리 — 진로 화면은 파트 분해 없이 단일 text.
    """
    text = serializers.CharField()


class ReadinessItemSerializer(serializers.Serializer):
    """② 현재 취업 준비도 평가 한 칸 (#171).

    status: ok / warn / tip (프론트 아이콘 ✅/⚠️/💡 매핑).
    전공 기초만 grounded(졸업요건), 실무·인턴십은 직무 템플릿 고정값.
    """
    status = serializers.CharField()
    label = serializers.CharField()
    message = serializers.CharField()


class RoadmapStepSerializer(serializers.Serializer):
    """③ 단계별 로드맵 STEP 1개 (#171).

    lines  : 템플릿 텍스트 줄 (STEP 5 학기전략은 빈 배열).
    courses: STEP 5만 채움 — 5.3.1 추천 과목 (그 외 STEP은 빈 배열).
    """
    step = serializers.IntegerField()
    title = serializers.CharField()
    lines = serializers.ListField(child=serializers.CharField())
    courses = RecommendedCourseSerializer(many=True)


class CareerRoadmapSerializer(serializers.Serializer):
    """취업·진로 로드맵 테마 상세 응답 (#171, Figma 221-5848).

    직무별 템플릿 + grounded 2조각(전공기초 status / STEP5 추천과목).
    note: 직무 미입력(NO_CAREER_GOAL)·미지원(UNSUPPORTED_CAREER_GOAL) 시 머신 코드, 정상 시 null.
    advice/readiness/roadmap/quick_questions는 note 있으면 빈 값.
    """
    target_job = serializers.CharField(allow_blank=True)
    note = serializers.CharField(allow_null=True)
    target_year = serializers.IntegerField()
    target_semester = serializers.IntegerField()
    advice = CareerAdviceSerializer(allow_null=True)        # ① 띵똥이의 조언
    readiness = ReadinessItemSerializer(many=True)          # ② 취업 준비도 평가
    roadmap = RoadmapStepSerializer(many=True)              # ③ 단계별 로드맵 STEP 1~6
    quick_questions = QuickQuestionSerializer(many=True)    # ④ 띵똥이에게 물어보기


# ────────────────────────────────────────
# 교환학생·해외 인턴십 가이드 테마 상세 (이슈 #180, Figma 221-6066)
# ────────────────────────────────────────

class ExchangeAdviceSerializer(serializers.Serializer):
    """② 띵똥이의 조언 — (학년, 학기) 템플릿 1문장 (#180).

    #171 CareerAdviceSerializer(text)와 분리 — 이 화면은 stage_message 단일 키.
    """
    stage_message = serializers.CharField()


class NecessityItemSerializer(serializers.Serializer):
    """③ 나에게 필요한 선택일까 — 옵션 1개 별점 (#180).

    score: 1~5 (프론트가 ⭐ 채움/빈칸 렌더). reason: 옵션×점수밴드 고정 한줄이유.
    """
    option = serializers.CharField()
    icon = serializers.CharField()
    score = serializers.IntegerField()
    reason = serializers.CharField()


class EvaluationItemSerializer(serializers.Serializer):
    """④ 판단 기준에 따른 평가 — 옵션 1개 적합 조건 (#180).

    fits: 학년 스테이지 base 조건 + 관심사 매칭 시 bullet 1개 추가.
    interest_note: 관심사 매칭 시 안내 1줄, 미매칭이면 null.
    """
    option = serializers.CharField()
    icon = serializers.CharField()
    fits = serializers.ListField(child=serializers.CharField())
    caveat = serializers.CharField()
    interest_note = serializers.CharField(allow_null=True)


class CurrentRecommendationItemSerializer(serializers.Serializer):
    """⑤ 지금 시점 기준 추천 — 우선순위 1개 (#180)."""
    rank = serializers.IntegerField()
    title = serializers.CharField()
    note = serializers.CharField()


class ExchangeGuideSerializer(serializers.Serializer):
    """교환학생·해외 인턴십 가이드 테마 상세 응답 (#180, Figma 221-6066).

    전부 규칙 기반 템플릿 (LLM 없음). 학년·학기(메인) + 관심사(보조).
    """
    advice = ExchangeAdviceSerializer()                              # ② 조언
    necessity = NecessityItemSerializer(many=True)                  # ③ 별점
    evaluation = EvaluationItemSerializer(many=True)                # ④ 판단 기준
    current_recommendation = CurrentRecommendationItemSerializer(many=True)  # ⑤ 시점 추천
    quick_questions = QuickQuestionSerializer(many=True)            # ⑥ 물어보기


class StudyTipSerializer(serializers.Serializer):
    """③④ 꿀팁 카드 1개 — 섹션 안의 제목+내용 (학업 스트레스 & 시간관리 꿀팁, #190).

    emoji: 카드 머리 아이콘 / title: 카드 제목 / body: 1~2줄 설명.
    """
    emoji = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()


class StudyTipSectionSerializer(serializers.Serializer):
    """③④ 꿀팁 섹션 1개 — 섹션 제목 + 카드 목록 (Figma 221-5848 "단계별 로드맵" 형태)."""
    title = serializers.CharField()
    tips = StudyTipSerializer(many=True)


class StudyTipsSerializer(serializers.Serializer):
    """학업 스트레스 & 시간관리 꿀팁 테마 상세 응답 (Figma 119-1036 카드 진입, #190).

    전부 정적 콘텐츠 (개인화·LLM 없음). 누구에게나 동일.
    """
    advice = serializers.CharField()                       # ② 띵똥이의 한마디
    sections = StudyTipSectionSerializer(many=True)        # ③④ 꿀팁 섹션
    quick_questions = QuickQuestionSerializer(many=True)   # ⑤ 질문칩


class SemesterPlanSerializer(serializers.Serializer):
    """학기 1개 응답 — 학칙 7분류로 분리 (spec 5.3.2, #47 Phase 3).

    semester 값: 1/2 정규학기, 3 하계 계절학기, 4 동계 계절학기.
    빈 카테고리도 키는 유지 (빈 배열). 프론트가 키 존재 체크 안 해도 됨.
    """
    year = serializers.IntegerField()
    semester = serializers.IntegerField()
    major_required = RecommendedCourseSerializer(many=True)       # 전공필수
    major_elective = RecommendedCourseSerializer(many=True)       # 전공선택
    liberal_common = RecommendedCourseSerializer(many=True)       # 공통교양
    liberal_core = RecommendedCourseSerializer(many=True)         # 핵심교양
    liberal_foundation = RecommendedCourseSerializer(many=True)   # 학문기초교양
    liberal_general = RecommendedCourseSerializer(many=True)      # 일반교양
    free_elective = RecommendedCourseSerializer(many=True)        # 자유선택


class CurriculumPlanSerializer(serializers.Serializer):
    plan_number = serializers.IntegerField()
    max_credits = serializers.IntegerField()                    # 이 plan의 학점 상한
    semesters = SemesterPlanSerializer(many=True)
