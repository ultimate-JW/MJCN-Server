# MJCN 서버 구조 다이어그램 (상세설계보고서용)

> 명지대학교 학생 AI 비서 서비스 API 서버 아키텍처

---

## 피드백 개요
- 다 포함하는 것 보다는 주요한 것 3가지를 추가하는 게 좋아보입니다.

### 포함 제안 목록 & 흐름 설명
아래 3단 구조 설명으로 흐름을 구성하면 어떨까 합니다.
1. 전체 시스템 구성도 → 시스템 간 연결 구조
    - 프론트엔드의 내용이 포함됨
    - 아래 흐름을 설명
        - Android / iOS → 요청 보냄
        - 서버 → 처리
        - AI 서버 → 응답 생성
    - 기존 다이어그램 : "1. 전체 시스템 구성도 (System Context)" 해당

2. 요청 처리 흐름 → 실제 동작 과정
    - 서버 중심 설명
    - 포함 내용: 인증, 로직 처리, DB, AI 호출
    - 기존 다이어그램 : "4. 요청 처리 흐름 (Request Flow)" 해당

3. 계층형 아키텍처 → 서버 내부 설계
    - 포함 내용: API, Business, AI, Data
    - 기존 다이어그램 : "2. 계층형 아키텍처 (Layered Architecture)" 해당

이후 발표 가이드 및 구조도 피드백에 대한 상세한 내용은
아래 각 파트별로 진행합니다.

---

## 전체 시스템 구성도
- 기존 다이어그램 : "1. 전체 시스템 구성도 (System Context)" 해당

### 발표 가이드
- 모바일 앱에서 요청이 들어오면 Django 서버에서 처리
- DB와 캐시를 통해 데이터 관리
- 카카오 로그인, 이메일 같은 외부 서비스와 연동됨

### 구조도 피드백
- 특별히 없음

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

## 2. 요청 처리 흐름
- 기존 다이어그램 : "4. 요청 처리 흐름 (Request Flow)" 해당

### 발표 가이드
- 클라이언트 요청이 들어오면 인증을 먼저 확인하고
- Business Logic에서 데이터를 처리
- 필요 시 AI 서비스를 호출하여 결과를 생성
- 최종적으로 JSON 형태로 응답 반환

### 구조도 피드백
#### 수정 전

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

### 수정 후
```mermaid
sequenceDiagram
    autonumber
    participant C as 클라이언트
    participant API as API 서버
    participant AUTH as 인증
    participant BL as Business Logic
    participant AI as AI 서비스
    participant DB as 데이터베이스

    C->>API: 요청 전송 (HTTPS)
    
    API->>AUTH: JWT 인증 확인
    AUTH-->>API: 인증 결과

    API->>BL: 요청 전달
    BL->>DB: 데이터 조회/저장

    alt AI 기능 요청
        BL->>AI: 질의/추천 요청
        AI-->>BL: 결과 반환
    end

    BL-->>API: 처리 결과
    API-->>C: JSON 응답
```

---

## 3. 계층형 아키텍처
- 기존 다이어그램 : "2. 계층형 아키텍처 (Layered Architecture)" 해당

### 발표 가이드
- 클라이언트 요청은 API Layer에서 처리
- Business Logic Layer에서 핵심 로직 수행
- AI Service Layer에서 추천 및 질의응답 처리
- Data Layer에서 데이터 저장 및 조회

### 구조도 피드백
- 기존 구조: Django 내부 구조를 설명하는 방향(Framework 중심)
- 제안 구조: 서비스 기준 아키텍처(기능 중심)

#### 수정 전

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

#### 수정 후
```mermaid
flowchart TB
    subgraph Client["Client Layer"]
        APP["모바일 앱"]
    end

    subgraph API["API Layer"]
        DJ["Django + DRF"]
    end

    subgraph Business["Business Logic Layer"]
        BL["비즈니스 로직 처리 (공지 필터링, 사용자 상태 반영)"]
    end

    subgraph AI["AI Service Layer"]
        AIS["AI 처리 (질의응답, 추천, 요약)"]
    end

    subgraph Data["Data Layer"]
        DB[("RDB")]
        CACHE["Cache"]
    end

    APP --> DJ
    DJ --> BL
    BL --> AIS
    BL --> DB
    AIS --> DB
    BL --> CACHE
```

---
