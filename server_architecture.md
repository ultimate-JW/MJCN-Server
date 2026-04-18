# MJCN 서버 구조 다이어그램 (상세설계보고서용)

> 명지대학교 학생 AI 비서 서비스 API 서버 아키텍처

---

## 1. 전체 시스템 구성도 (System Context)

```mermaid
flowchart LR
    subgraph Clients["클라이언트"]
        MOB["모바일 앱"]
        WEB["웹 브라우저<br/>(Swagger UI)"]
    end

    subgraph External["외부 연동"]
        KAKAO["Kakao OAuth<br/>(카카오 로그인)"]
        SMTP["Gmail SMTP<br/>(이메일 발송)"]
    end

    subgraph Server["MJCN API 서버"]
        DJANGO["Django 5.2 + DRF"]
        CACHE["캐시<br/>(LocMem / Redis)"]
        DB[("RDB<br/>SQLite / PostgreSQL")]
    end

    MOB -->|HTTPS / JSON| DJANGO
    WEB -->|HTTPS / JSON| DJANGO
    DJANGO <-->|OAuth 2.0| KAKAO
    DJANGO -->|이메일 코드 발송| SMTP
    DJANGO <-->|JWT 블랙리스트| CACHE
    DJANGO <-->|ORM| DB
```

---

## 2. 계층형 아키텍처 (Layered Architecture)

```mermaid
flowchart TB
    subgraph Presentation["Presentation Layer"]
        URLS["URL Router<br/>(CapstoneDesign/urls.py)"]
        SWAGGER["drf-spectacular<br/>(API 문서)"]
    end

    subgraph MW["Middleware Layer"]
        SEC["SecurityMiddleware"]
        CORS["CorsMiddleware"]
        CSRF["CsrfViewMiddleware"]
        AUTHMW["AuthenticationMiddleware"]
    end

    subgraph DRF["DRF Layer"]
        AUTH["BlacklistCheckJWTAuthentication<br/>(Custom JWT + 블랙리스트)"]
        PERM["IsAuthenticated / IsOwner"]
        THROT["Throttle<br/>(anon 30/m, user 60/m,<br/>verify_email 5/m)"]
        PAGIN["StandardPagination"]
    end

    subgraph Apps["Application Layer (Django Apps)"]
        ACC["accounts<br/>views / serializers / services"]
        CRS["courses<br/>views / serializers"]
        COM["common<br/>permissions / pagination"]
    end

    subgraph Data["Data Layer"]
        ORM["Django ORM"]
        RDB[("RDB")]
        CACHEBE["Cache Backend"]
    end

    URLS --> MW
    SWAGGER --> URLS
    MW --> DRF
    DRF --> Apps
    Apps --> ORM
    ORM --> RDB
    AUTH -.블랙리스트 조회.-> CACHEBE
```

---

## 3. Django 앱 구성도 (Application Modules)

```mermaid
flowchart LR
    subgraph Project["CapstoneDesign (프로젝트 루트)"]
        SETTINGS["settings.py"]
        ROOTURL["urls.py"]
        WSGI["wsgi.py / asgi.py"]
    end

    subgraph Accounts["accounts — 사용자/인증"]
        A_V["views.py<br/>signup · login · kakao<br/>profile · settings · withdraw"]
        A_S["serializers.py"]
        A_SV["services.py<br/>이메일 인증 코드 생성/검증"]
        A_AU["authentication.py<br/>JWT 블랙리스트"]
        A_TH["throttles.py<br/>EmailScoped · PasswordResetScoped"]
        A_M["models.py<br/>User · InterestArea<br/>CourseHistory · CurrentCourse<br/>EmailVerification"]
        A_U["urls.py"]
    end

    subgraph Courses["courses — 교과/졸업요건"]
        C_V["views.py<br/>CourseSearch · CompletionStatus<br/>NextSemesterRecommend<br/>CurriculumRecommend"]
        C_S["serializers.py"]
        C_M["models.py<br/>Course · CoursePrerequisite<br/>CourseSchedule<br/>GraduationRequirement<br/>AcademicCalendar"]
        C_U["urls.py"]
    end

    subgraph Common["common — 공통 유틸"]
        CM_PG["pagination.py"]
        CM_PM["permissions.py"]
    end

    ROOTURL --> A_U
    ROOTURL --> C_U
    A_U --> A_V
    A_V --> A_S
    A_V --> A_SV
    A_V --> A_AU
    A_V --> A_M
    A_V --> A_TH
    C_U --> C_V
    C_V --> C_S
    C_V --> C_M
    A_V -.페이지네이션.-> CM_PG
    C_V -.페이지네이션.-> CM_PG
    A_V -.권한.-> CM_PM
    C_V -.권한.-> CM_PM
```

---

## 4. 요청 처리 흐름 (Request Flow)

```mermaid
sequenceDiagram
    autonumber
    participant C as 클라이언트
    participant MW as Middleware
    participant AU as JWT Auth<br/>(BlacklistCheck)
    participant CA as Cache<br/>(블랙리스트)
    participant PE as Permission<br/>/ Throttle
    participant V as View
    participant SE as Serializer
    participant OR as ORM
    participant DB as RDB

    C->>MW: HTTPS 요청<br/>Authorization: Bearer {access}
    MW->>AU: JWT 검증
    AU->>CA: jti 블랙리스트 조회
    CA-->>AU: hit / miss
    alt 블랙리스트 hit
        AU-->>C: 401 InvalidToken
    else 정상
        AU->>PE: 인증 완료
        PE->>PE: IsAuthenticated · Throttle 검사
        alt Throttle 초과
            PE-->>C: 429 Too Many Requests
        else 통과
            PE->>V: dispatch
            V->>SE: 요청 데이터 검증
            SE-->>V: validated_data
            V->>OR: queryset / save
            OR->>DB: SQL
            DB-->>OR: rows
            OR-->>V: model 인스턴스
            V->>SE: 응답 직렬화
            SE-->>V: JSON
            V-->>C: 200 / 201 / 204
        end
    end
```

---

## 5. 인증·세션 컴포넌트 (JWT + Access Token 블랙리스트)

```mermaid
flowchart LR
    subgraph Issue["토큰 발급"]
        LOGIN["POST /login<br/>POST /login/kakao"]
        LOGIN --> SJWT["SimpleJWT<br/>access(30m) / refresh(7d)"]
    end

    subgraph Verify["요청 시 검증"]
        REQ["인증 요청"]
        REQ --> JWTAU["BlacklistCheckJWTAuthentication"]
        JWTAU -->|jti 확인| CACHE["Cache<br/>access_jti_blacklist:{jti}"]
    end

    subgraph Rotate["토큰 갱신"]
        RFR["POST /token/refresh"]
        RFR --> ROT["ROTATE_REFRESH_TOKENS=True<br/>BLACKLIST_AFTER_ROTATION=True"]
        ROT --> DBBL[("simplejwt<br/>token_blacklist 테이블")]
    end

    subgraph Logout["로그아웃 / 탈퇴"]
        LO["POST /logout<br/>POST /withdraw"]
        LO --> BLA["blacklist_current_access_token()<br/>(남은 TTL만큼 캐시 등록)"]
        BLA --> CACHE
        LO --> DBBL
    end
```

**보안 포인트**
- `ACCESS_TOKEN_LIFETIME = 30m`, `REFRESH_TOKEN_LIFETIME = 7d`
- Refresh rotation + 회전 즉시 구(舊) refresh 블랙리스트화
- Access token도 `jti` 기반 캐시 블랙리스트로 즉시 무효화 가능
- 운영 배포 시 **Redis 등 공유 캐시 백엔드 필수** (멀티 워커 환경에서 LocMem은 워커별 독립 메모리라 블랙리스트가 공유되지 않음)

---

## 6. 엔드포인트 맵 (URL Routing)

```mermaid
flowchart LR
    ROOT["/"] --> ADMIN["/admin/"]
    ROOT --> SCHEMA["/api/schema/"]
    ROOT --> DOCS["/api/docs/<br/>(Swagger UI)"]
    ROOT --> V1["/api/v1/"]

    V1 --> ACC["/accounts/"]
    V1 --> CRS["/courses/"]

    subgraph AccountsEP["accounts 엔드포인트"]
        ACC --> SIGN["signup/"]
        ACC --> VER["verify-email/ · resend/"]
        ACC --> LOG["login/ · login/kakao/"]
        ACC --> TOK["token/refresh/"]
        ACC --> OUT["logout/ · withdraw/"]
        ACC --> PWD["password/reset/ · verify/ · confirm/"]
        ACC --> PRO["profile/ · settings/"]
        ACC --> INT["interests/ (ViewSet)"]
        ACC --> HIS["course-history/ (ViewSet)"]
        ACC --> CUR["current-courses/ (ViewSet)"]
    end

    subgraph CoursesEP["courses 엔드포인트"]
        CRS --> SRC["(list) 과목 검색"]
        CRS --> STA["status/ 이수현황"]
        CRS --> NXT["recommend/next/ 다음학기"]
        CRS --> CRM["recommend/curriculum/ 커리큘럼"]
    end
```

---

## 7. 데이터 계층 개요 (Storage Components)

```mermaid
flowchart TB
    subgraph App["Django Application"]
        ORM["Django ORM"]
        CACHEAPI["Django Cache API"]
        MAILAPI["Django Mail API"]
    end

    subgraph Persistence["영속 저장소"]
        RDB[("RDB<br/>dev: SQLite<br/>prod: PostgreSQL 권장")]
        MEDIA[["MEDIA_ROOT<br/>(파일 저장)"]]
    end

    subgraph Ephemeral["휘발성 저장소"]
        LOCMEM["LocMemCache<br/>(개발 기본값)"]
        REDIS[("Redis<br/>(운영 권장)")]
    end

    subgraph Outbound["외부 서비스"]
        SMTP["Gmail SMTP<br/>(smtp.gmail.com:587, TLS)"]
        CONSOLE["Console Backend<br/>(자격증명 없을 때)"]
    end

    ORM --> RDB
    ORM --> MEDIA
    CACHEAPI --> LOCMEM
    CACHEAPI -.운영.-> REDIS
    MAILAPI --> SMTP
    MAILAPI -.자격증명 없음.-> CONSOLE
```

---

## 8. 배포 토폴로지 (Deployment View, 권장 구성)

```mermaid
flowchart LR
    USER[["사용자 기기"]]

    subgraph Edge["Edge / CDN"]
        LB["HTTPS 로드밸런서<br/>TLS 종단"]
    end

    subgraph AppTier["Application Tier"]
        GU1["gunicorn worker #1<br/>(Django)"]
        GU2["gunicorn worker #2<br/>(Django)"]
        GUN["gunicorn worker #N"]
    end

    subgraph DataTier["Data Tier"]
        PG[("PostgreSQL<br/>Primary")]
        PGR[("PostgreSQL<br/>Replica (선택)")]
        REDIS[("Redis<br/>JWT 블랙리스트 · Throttle")]
        OBJ[["Object Storage<br/>(MEDIA, 선택)"]]
    end

    subgraph Ext["외부 서비스"]
        KAKAO["Kakao OAuth"]
        SMTP["Gmail SMTP"]
    end

    USER -->|HTTPS| LB
    LB --> GU1
    LB --> GU2
    LB --> GUN
    GU1 --> PG
    GU2 --> PG
    GUN --> PG
    PG -.replication.-> PGR
    GU1 --> REDIS
    GU2 --> REDIS
    GUN --> REDIS
    GU1 -.선택.-> OBJ
    GU1 --> KAKAO
    GU1 --> SMTP
```

---

## 9. 기술 스택 요약

| 계층 | 구성 요소 | 버전 / 비고 |
|---|---|---|
| 언어 / 런타임 | Python | 3.x |
| 웹 프레임워크 | Django | 5.2.12 |
| API 프레임워크 | Django REST Framework | 3.17.1 |
| 인증 | djangorestframework-simplejwt + Custom Blacklist | 5.5.1 / access·refresh 블랙리스트 이중화 |
| API 문서 | drf-spectacular (OpenAPI 3) | 0.29.0 |
| CORS | django-cors-headers | 4.9.0 |
| DB (개발) | SQLite | 내장 |
| DB (운영 권장) | PostgreSQL | — |
| 캐시 (개발) | LocMemCache | 프로세스 로컬 |
| 캐시 (운영 권장) | Redis | 블랙리스트·Throttle 공유 필수 |
| 이메일 | Gmail SMTP (smtp.gmail.com:587, TLS) | Console backend fallback |
| OAuth | Kakao 로그인 | accounts/kakao 엔드포인트 |
| WSGI 서버 (권장) | gunicorn | 멀티 워커 |

---

## 10. 비기능 요구사항 매핑

| 비기능 요구사항 | 구현 위치 | 비고 |
|---|---|---|
| 인증/인가 | `accounts.authentication.BlacklistCheckJWTAuthentication` | JWT + jti 블랙리스트 |
| 속도 제한 | DRF Throttle (`anon 30/m`, `user 60/m`, `verify_email 5/m`, `password_reset 5/m`) | Brute force 방어 |
| 세션 즉시 종료 | `blacklist_current_access_token()` | logout/withdraw 시 access token 무효화 |
| 이메일 인증 | `accounts.services.send_verification_email` | 6자리 코드, 3분 만료, 트랜잭션 원자성 |
| 동시성 제어 | `select_for_update()` + 트랜잭션 | EmailVerification race condition 방지 |
| API 문서 | drf-spectacular `/api/docs/` | OpenAPI 3 |
| 페이지네이션 | `common.pagination.StandardPagination` | page_size=20 |
