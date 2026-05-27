from datetime import time

from django.test import TestCase

from courses.models import (
    Course,
    CourseOffering,
    CourseSchedule,
    TIMETABLE_SLOT_BITS_PER_DAY,
)
from timetables.services.combining import (
    generate_combinations,
    group_offerings_by_course,
)
from timetables.services.filtering import (
    expand_to_offerings,
    filter_offerings_by_prefs,
    passes_prefs_hard_filter,
)
from timetables.utils.conflict import (
    has_any_conflict,
    is_compatible,
    merge_bitmaps,
)


def _make_course(**overrides):
    defaults = dict(
        course_code='CSE_T001',
        name='시간표테스트과목',
        college='ICT융합대학',
        department='컴퓨터정보통신공학부',
        major='컴퓨터공학전공',
        category='전공필수',
        credits=3,
        year_open=1,
        semester_open=1,
    )
    defaults.update(overrides)
    return Course.objects.create(**defaults)


def _make_offering(course, section_no='01', year=2026, semester=1):
    return CourseOffering.objects.create(
        course=course,
        year=year,
        semester=semester,
        section_no=section_no,
    )


def _add_schedule(offering, day, start, end):
    return CourseSchedule.objects.create(
        course=offering.course,
        offering=offering,
        day_of_week=day,
        start_time=start,
        end_time=end,
    )


# CourseOffering.time_bitmap (#97 5.3.6) 정확성 + combination-level helper 검증
class ConflictDetectionTests(TestCase):
    def test_단일_schedule_bitmap_정확(self):
        c = _make_course(course_code='CSE_T001')
        o = _make_offering(c)
        _add_schedule(o, '월', time(10, 0), time(11, 50))  # 월 slot 2~5
        bits = o.time_bitmap
        # 월(day 0) slot 2,3,4,5 점유 → bit (0*32+2 ~ 0*32+5) 4개 set
        for slot in range(2, 6):
            self.assertNotEqual(bits & (1 << (0 * TIMETABLE_SLOT_BITS_PER_DAY + slot)), 0,
                                msg=f'slot {slot} 점유 비트 누락')
        # 점유 외 비트(slot 1, 6)는 0
        self.assertEqual(bits & (1 << (0 * TIMETABLE_SLOT_BITS_PER_DAY + 1)), 0)
        self.assertEqual(bits & (1 << (0 * TIMETABLE_SLOT_BITS_PER_DAY + 6)), 0)

    def test_여러_schedule_같은_offering_OR_합성(self):
        c = _make_course(course_code='CSE_T002')
        o = _make_offering(c)
        _add_schedule(o, '월', time(10, 0), time(11, 0))  # 월 slot 2~3
        _add_schedule(o, '수', time(10, 0), time(11, 0))  # 수 slot 2~3
        bits = o.time_bitmap
        # 월(0) slot 2,3 + 수(2) slot 2,3 set
        self.assertNotEqual(bits & (1 << (0 * TIMETABLE_SLOT_BITS_PER_DAY + 2)), 0)
        self.assertNotEqual(bits & (1 << (2 * TIMETABLE_SLOT_BITS_PER_DAY + 2)), 0)
        # 화(1), 목(3) 비트는 0
        self.assertEqual(bits & (1 << (1 * TIMETABLE_SLOT_BITS_PER_DAY + 2)), 0)
        self.assertEqual(bits & (1 << (3 * TIMETABLE_SLOT_BITS_PER_DAY + 2)), 0)

    def test_75분_강의_보수적_ceil(self):
        """09:00-10:15 75분 강의 → 09:00-09:30, 09:30-10:00, 10:00-10:30 (3 slot) 점유."""
        c = _make_course(course_code='CSE_T003')
        o = _make_offering(c)
        _add_schedule(o, '화', time(9, 0), time(10, 15))
        bits = o.time_bitmap
        for slot in range(0, 3):
            self.assertNotEqual(bits & (1 << (1 * TIMETABLE_SLOT_BITS_PER_DAY + slot)), 0,
                                msg=f'화 slot {slot} 점유 비트 누락 (75분 보수적)')
        # 화 slot 3(10:30~)은 비점유
        self.assertEqual(bits & (1 << (1 * TIMETABLE_SLOT_BITS_PER_DAY + 3)), 0)

    def test_다른_요일_같은_시간_충돌_없음(self):
        c1 = _make_course(course_code='CSE_T010')
        c2 = _make_course(course_code='CSE_T011')
        o1 = _make_offering(c1)
        o2 = _make_offering(c2, section_no='02')
        _add_schedule(o1, '월', time(10, 0), time(11, 50))
        _add_schedule(o2, '화', time(10, 0), time(11, 50))
        self.assertTrue(is_compatible(o1.time_bitmap, o2.time_bitmap))

    def test_같은_요일_다른_시간_충돌_없음(self):
        c1 = _make_course(course_code='CSE_T020')
        c2 = _make_course(course_code='CSE_T021')
        o1 = _make_offering(c1)
        o2 = _make_offering(c2, section_no='02')
        _add_schedule(o1, '월', time(9, 0), time(10, 0))    # slot 0~1
        _add_schedule(o2, '월', time(10, 0), time(11, 0))   # slot 2~3 (인접, 안 겹침)
        self.assertTrue(is_compatible(o1.time_bitmap, o2.time_bitmap))

    def test_같은_요일_같은_시간_충돌(self):
        c1 = _make_course(course_code='CSE_T030')
        c2 = _make_course(course_code='CSE_T031')
        o1 = _make_offering(c1)
        o2 = _make_offering(c2, section_no='02')
        _add_schedule(o1, '월', time(10, 0), time(11, 50))
        _add_schedule(o2, '월', time(10, 0), time(11, 50))
        self.assertFalse(is_compatible(o1.time_bitmap, o2.time_bitmap))

    def test_부분_겹침_충돌(self):
        c1 = _make_course(course_code='CSE_T040')
        c2 = _make_course(course_code='CSE_T041')
        o1 = _make_offering(c1)
        o2 = _make_offering(c2, section_no='02')
        _add_schedule(o1, '월', time(10, 0), time(11, 50))   # slot 2~5
        _add_schedule(o2, '월', time(11, 0), time(12, 50))   # slot 4~7 (slot 4,5 겹침)
        self.assertFalse(is_compatible(o1.time_bitmap, o2.time_bitmap))

    def test_30분_경계_정확(self):
        """11:00 종료 vs 11:00 시작은 슬롯 경계 정확히 안 겹쳐야 함."""
        c1 = _make_course(course_code='CSE_T050')
        c2 = _make_course(course_code='CSE_T051')
        o1 = _make_offering(c1)
        o2 = _make_offering(c2, section_no='02')
        _add_schedule(o1, '월', time(10, 0), time(11, 0))   # slot 2~3
        _add_schedule(o2, '월', time(11, 0), time(12, 0))   # slot 4~5
        self.assertTrue(is_compatible(o1.time_bitmap, o2.time_bitmap))

    def test_has_any_conflict_3개_중_충돌(self):
        c = _make_course(course_code='CSE_T060')
        oA = _make_offering(_make_course(course_code='A'), section_no='A')
        oB = _make_offering(_make_course(course_code='B'), section_no='B')
        oC = _make_offering(_make_course(course_code='C'), section_no='C')
        _add_schedule(oA, '월', time(10, 0), time(11, 0))
        _add_schedule(oB, '화', time(10, 0), time(11, 0))
        _add_schedule(oC, '월', time(10, 30), time(11, 30))  # oA와 겹침
        bitmaps = [oA.time_bitmap, oB.time_bitmap, oC.time_bitmap]
        self.assertTrue(has_any_conflict(bitmaps))

    def test_has_any_conflict_충돌_없음(self):
        oA = _make_offering(_make_course(course_code='A'), section_no='A')
        oB = _make_offering(_make_course(course_code='B'), section_no='B')
        oC = _make_offering(_make_course(course_code='C'), section_no='C')
        _add_schedule(oA, '월', time(10, 0), time(11, 0))
        _add_schedule(oB, '화', time(10, 0), time(11, 0))
        _add_schedule(oC, '수', time(10, 0), time(11, 0))
        bitmaps = [oA.time_bitmap, oB.time_bitmap, oC.time_bitmap]
        self.assertFalse(has_any_conflict(bitmaps))

    def test_merge_bitmaps_N개_정확(self):
        oA = _make_offering(_make_course(course_code='A'), section_no='A')
        oB = _make_offering(_make_course(course_code='B'), section_no='B')
        _add_schedule(oA, '월', time(10, 0), time(11, 0))
        _add_schedule(oB, '화', time(10, 0), time(11, 0))
        merged = merge_bitmaps([oA.time_bitmap, oB.time_bitmap])
        self.assertEqual(merged, oA.time_bitmap | oB.time_bitmap)

    def test_DFS_백트래킹_XOR_정확(self):
        """OR로 push한 비트를 XOR로 pop하면 원래 상태 복귀."""
        oA = _make_offering(_make_course(course_code='A'), section_no='A')
        oB = _make_offering(_make_course(course_code='B'), section_no='B')
        _add_schedule(oA, '월', time(10, 0), time(11, 0))
        _add_schedule(oB, '화', time(10, 0), time(11, 0))
        acc = 0
        acc |= oA.time_bitmap  # push A
        acc |= oB.time_bitmap  # push B
        acc ^= oB.time_bitmap  # pop B
        self.assertEqual(acc, oA.time_bitmap)
        acc ^= oA.time_bitmap  # pop A
        self.assertEqual(acc, 0)

    def test_모르는_요일_skip(self):
        """토/일은 DAY_IDX 매핑에 없어 무시 (실제로는 DAY_CHOICES가 막아주지만 방어)."""
        c = _make_course(course_code='CSE_T070')
        o = _make_offering(c)
        _add_schedule(o, '월', time(10, 0), time(11, 0))
        # 정상 schedule만 반영됨
        self.assertNotEqual(o.time_bitmap, 0)


# STEP 3 (분반 확장) + STEP 4 (prefs hard filter) 검증
class PipelineFilterTests(TestCase):
    def test_expand_to_offerings_여러_분반(self):
        c = _make_course(course_code='F001')
        o1 = _make_offering(c, section_no='01')
        o2 = _make_offering(c, section_no='02')
        result = expand_to_offerings([c], 2026, 1)
        self.assertEqual({o.id for o in result}, {o1.id, o2.id})

    def test_expand_to_offerings_학기_불일치_제외(self):
        c = _make_course(course_code='F002')
        target = _make_offering(c, section_no='01', year=2026, semester=1)
        _make_offering(c, section_no='02', year=2026, semester=2)   # 다른 학기
        _make_offering(c, section_no='03', year=2025, semester=1)   # 다른 연도
        result = expand_to_offerings([c], 2026, 1)
        self.assertEqual([o.id for o in result], [target.id])

    def test_expand_to_offerings_빈_입력(self):
        self.assertEqual(expand_to_offerings([], 2026, 1), [])

    def test_prefs_기본값_모두_통과(self):
        c = _make_course(course_code='F010')
        o = _make_offering(c)
        _add_schedule(o, '월', time(9, 0), time(10, 50))   # 1교시 시작
        # 모든 prefs default(false) → 통과
        self.assertTrue(passes_prefs_hard_filter(o, {}))

    def test_no_morning_1교시_제외(self):
        c = _make_course(course_code='F011')
        o = _make_offering(c)
        _add_schedule(o, '월', time(9, 0), time(10, 0))   # 09:00 시작 = 1교시
        self.assertFalse(passes_prefs_hard_filter(o, {'no_morning': True}))

    def test_no_morning_10시_시작은_통과(self):
        """경계: 10:00 정각 시작은 1교시 아님 → 통과."""
        c = _make_course(course_code='F012')
        o = _make_offering(c)
        _add_schedule(o, '월', time(10, 0), time(11, 0))
        self.assertTrue(passes_prefs_hard_filter(o, {'no_morning': True}))

    def test_no_evening_18시_시작_제외(self):
        c = _make_course(course_code='F013')
        o = _make_offering(c)
        _add_schedule(o, '월', time(18, 0), time(19, 50))
        self.assertFalse(passes_prefs_hard_filter(o, {'no_evening': True}))

    def test_no_evening_17시30분_시작은_통과(self):
        """경계: 17:30 시작은 야간 아님 → 통과."""
        c = _make_course(course_code='F014')
        o = _make_offering(c)
        _add_schedule(o, '월', time(17, 30), time(19, 20))
        self.assertTrue(passes_prefs_hard_filter(o, {'no_evening': True}))

    def test_banned_days_요일_매칭_제외(self):
        c = _make_course(course_code='F015')
        o = _make_offering(c)
        _add_schedule(o, '금', time(10, 0), time(11, 50))
        self.assertFalse(passes_prefs_hard_filter(o, {'banned_days': ['금']}))

    def test_banned_days_요일_미매칭_통과(self):
        c = _make_course(course_code='F016')
        o = _make_offering(c)
        _add_schedule(o, '월', time(10, 0), time(11, 50))
        self.assertTrue(passes_prefs_hard_filter(o, {'banned_days': ['금']}))

    def test_복합_prefs_하나라도_위반시_제외(self):
        """월수 10-11시 강의 + no_morning false + banned=['수'] → 수 요일 매칭으로 제외."""
        c = _make_course(course_code='F017')
        o = _make_offering(c)
        _add_schedule(o, '월', time(10, 0), time(11, 0))
        _add_schedule(o, '수', time(10, 0), time(11, 0))
        self.assertFalse(passes_prefs_hard_filter(o, {'banned_days': ['수']}))

    def test_filter_offerings_by_prefs_배치(self):
        c1 = _make_course(course_code='F020')
        c2 = _make_course(course_code='F021')
        c3 = _make_course(course_code='F022')
        good = _make_offering(c1, section_no='F100')
        bad_morning = _make_offering(c2, section_no='F101')
        bad_banned = _make_offering(c3, section_no='F102')
        _add_schedule(good, '월', time(10, 0), time(11, 0))
        _add_schedule(bad_morning, '월', time(9, 0), time(10, 0))    # 1교시
        _add_schedule(bad_banned, '금', time(10, 0), time(11, 0))    # banned 요일
        prefs = {'no_morning': True, 'banned_days': ['금']}
        result = filter_offerings_by_prefs([good, bad_morning, bad_banned], prefs)
        self.assertEqual([o.id for o in result], [good.id])


# STEP 5 (DFS 조합 생성) 검증
class CombinationGenerationTests(TestCase):
    def test_빈_후보_빈_결과(self):
        self.assertEqual(list(generate_combinations([], max_credits=18)), [])

    def test_단일_후보_한_조합(self):
        c = _make_course(course_code='G001', credits=3)
        o = _make_offering(c)
        _add_schedule(o, '월', time(10, 0), time(11, 0))
        result = list(generate_combinations([o], max_credits=18))
        self.assertEqual(result, [(o,)])

    def test_충돌없는_2개_3개_non_empty_조합(self):
        """2개 후보 → DFS가 {a}, {b}, {a,b} 세 non-empty 조합 yield."""
        c1 = _make_course(course_code='G010', credits=3)
        c2 = _make_course(course_code='G011', credits=3)
        o1 = _make_offering(c1, section_no='G10')
        o2 = _make_offering(c2, section_no='G11')
        _add_schedule(o1, '월', time(10, 0), time(11, 0))
        _add_schedule(o2, '화', time(10, 0), time(11, 0))
        combos = list(generate_combinations([o1, o2], max_credits=18))
        sets = {frozenset(c) for c in combos}
        self.assertEqual(sets, {
            frozenset({o1}),
            frozenset({o2}),
            frozenset({o1, o2}),
        })

    def test_충돌_2개_각각만_조합(self):
        c1 = _make_course(course_code='G020', credits=3)
        c2 = _make_course(course_code='G021', credits=3)
        o1 = _make_offering(c1, section_no='G20')
        o2 = _make_offering(c2, section_no='G21')
        _add_schedule(o1, '월', time(10, 0), time(11, 0))
        _add_schedule(o2, '월', time(10, 30), time(11, 30))   # 겹침
        combos = list(generate_combinations([o1, o2], max_credits=18))
        sets = {frozenset(c) for c in combos}
        # {o1, o2} 조합은 절대 X
        self.assertNotIn(frozenset({o1, o2}), sets)
        self.assertEqual(sets, {frozenset({o1}), frozenset({o2})})

    def test_같은_course_두_분반_OR_차단(self):
        """같은 Course 분반 2개는 어떤 조합에도 동시 등장 X."""
        c = _make_course(course_code='G030', credits=3)
        o_a = _make_offering(c, section_no='G30A')
        o_b = _make_offering(c, section_no='G30B')
        _add_schedule(o_a, '월', time(10, 0), time(11, 0))
        _add_schedule(o_b, '화', time(10, 0), time(11, 0))   # 시간 안 겹쳐도 같은 course
        combos = list(generate_combinations([o_a, o_b], max_credits=18))
        for combo in combos:
            self.assertFalse(
                {o_a, o_b}.issubset(set(combo)),
                msg=f'같은 course 분반 둘 동시 선택됨: {combo}',
            )
        # {o_a}, {o_b} 둘 다 가능
        sets = {frozenset(c) for c in combos}
        self.assertEqual(sets, {frozenset({o_a}), frozenset({o_b})})

    def test_max_credits_가지치기(self):
        """4과목(각 3학점) 후보, max=9 → 4과목 조합 절대 X."""
        offerings = []
        for i in range(4):
            c = _make_course(course_code=f'G04{i}', credits=3)
            o = _make_offering(c, section_no=f'G04{i}')
            _add_schedule(o, '월', time(9 + i, 0), time(9 + i, 50))
            offerings.append(o)
        combos = list(generate_combinations(offerings, max_credits=9))
        # 최대 3과목 = 9학점까지만
        max_size = max(len(c) for c in combos)
        self.assertEqual(max_size, 3)
        # 모든 조합 학점 합 ≤ 9
        for combo in combos:
            self.assertLessEqual(sum(o.course.credits for o in combo), 9)

    def test_min_credits_미달_조합도_yield(self):
        """min_credits soft (DFS는 안 거름, 점수 단계에서 패널티) — #97 결정."""
        c = _make_course(course_code='G050', credits=3)
        o = _make_offering(c)
        _add_schedule(o, '월', time(10, 0), time(11, 0))
        combos = list(generate_combinations([o], max_credits=18))
        # 3학점짜리 단일 조합이 그대로 yield됨 (min 무관)
        self.assertEqual(combos, [(o,)])

    def test_group_offerings_by_course_묶음(self):
        c1 = _make_course(course_code='G060')
        c2 = _make_course(course_code='G061')
        a1 = _make_offering(c1, section_no='G60A')
        a2 = _make_offering(c1, section_no='G60B')
        b1 = _make_offering(c2, section_no='G61A')
        groups = group_offerings_by_course([a1, a2, b1])
        self.assertEqual(len(groups), 2)
        # 같은 course의 분반은 한 그룹
        for g in groups:
            ids = {o.course_id for o in g}
            self.assertEqual(len(ids), 1)
