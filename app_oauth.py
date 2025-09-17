# app_oauth.py
# -----------------------------------------------------------
# Google Calendar + OAuth2 + (하드코딩) Alice/Bob + GPT 자연어 이벤트 생성
# -----------------------------------------------------------
import os, json, re
from datetime import datetime, timedelta, timezone

from flask import Flask, request, redirect, session
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from oauthlib.oauth2.rfc6749.errors import MismatchingStateError

# ─────────────────────────────
# Flask & 세션/프록시 설정
# ─────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "dev-secret")

# 크로스 사이트 리다이렉트에서도 세션 쿠키 전달(HTTPS 필수)
app.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=True,
)

# 프록시 뒤에서 스킴/호스트 보정(Render 등)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# 배포 도메인(https), 로컬 개발 시 http://localhost:5000
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
if BASE_URL.startswith("http://"):
    # 로컬 HTTP에서 OAuth 허용 (배포에서는 불필요)
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# ─────────────────────────────
# Google OAuth / Calendar 설정
# ─────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# GOOGLE_CLIENT_CONFIG_JSON 우선, 없으면 client_secret.json 파일 사용
if "GOOGLE_CLIENT_CONFIG_JSON" in os.environ:
    GOOGLE_CLIENT_CONFIG = json.loads(os.environ["GOOGLE_CLIENT_CONFIG_JSON"])
else:
    with open("client_secret.json", "r", encoding="utf-8") as f:
        GOOGLE_CLIENT_CONFIG = json.load(f)

TOKENS_DIR = "tokens"
os.makedirs(TOKENS_DIR, exist_ok=True)

def token_path(user: str) -> str:
    return os.path.join(TOKENS_DIR, f"{user}.json")

def save_credentials_for(user: str, creds: Credentials):
    with open(token_path(user), "w", encoding="utf-8") as f:
        f.write(creds.to_json())

def load_credentials_for(user: str):
    p = token_path(user)
    if not os.path.exists(p):
        return None
    return Credentials.from_authorized_user_file(p, SCOPES)

def build_service(creds: Credentials):
    return build("calendar", "v3", credentials=creds)

# 이름→이메일 매핑 (하드코딩 버전)
NAME_TO_EMAIL = {
    "alice": "compass0303@naver.com",
    "bob":   "kdwcompass33@gmail.com",
    "엘리스": "compass0303@naver.com",
    "밥":     "kdwcompass33@gmail.com",
}

def normalize_attendees(items):
    """이름이면 매핑, 이메일이면 그대로"""
    out = []
    for x in items or []:
        x = x.strip()
        if not x:
            continue
        if "@" in x:
            out.append({"email": x})
        else:
            out.append({"email": NAME_TO_EMAIL.get(x.lower(), x)})
    return out

# KST(Asia/Seoul)
KST = timezone(timedelta(hours=9))

# ─────────────────────────────
# OpenAI (자연어 → JSON)
# ─────────────────────────────
from openai import OpenAI
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def nlp_to_event_json(user_text: str) -> dict:
    """자연어 문장을 이벤트 JSON으로 변환 (오류 보강)"""
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    prompt = f"""
오늘은 {today_str} 입니다. 다음 한국어 문장을 분석해 캘린더 이벤트 JSON만 출력하세요.
반드시 JSON 하나만 출력하고, 추가 텍스트는 쓰지 마세요.
필드:
- summary: 문자열(없으면 '회의')
- start: ISO 8601 datetime with timezone (예: 2025-09-02T15:00:00+09:00)
- end:   ISO 8601 datetime with timezone
- attendees: 이메일 문자열 배열(없으면 빈 배열)
문장: "{user_text}"
"""

    # 1) OpenAI 호출
    try:
        resp = client.chat.completions.create(
        model="gpt-4o-mini",   # 비용/속도 좋은 최신 소형 모델 예시
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        )
        content = resp.choices[0].message.content
    except Exception as e:
        return {"error": f"OpenAI API 호출 실패: {str(e)}"}

    # 2) JSON 블록만 추출
    json_text = content.strip()
    m = re.search(r"\{.*\}", json_text, re.S)
    if m:
        json_text = m.group(0)

    # 3) 파싱
    try:
        parsed = json.loads(json_text)
    except Exception as e:
        return {"error": f"JSON 파싱 실패: {str(e)}\n응답 미리보기: {content[:200]}"}

    return parsed

# ─────────────────────────────
# 라우트: 홈/헬스체크/디버그
# ─────────────────────────────
@app.route("/")
def home():
    return (
        "<h2>Calendar Project (Alice/Bob 하드코딩)</h2>"
        "<ul>"
        "<li><a href='/auth/alice'>/auth/alice</a></li>"
        "<li><a href='/auth/bob'>/auth/bob</a></li>"
        "<li><a href='/whoami/alice'>/whoami/alice</a></li>"
        "<li><a href='/whoami/bob'>/whoami/bob</a></li>"
        "<li><a href='/make_test_event'>/make_test_event (me)</a></li>"
        "<li><a href='/make_test_event_multi'>/make_test_event_multi (alice+bob)</a></li>"
        "<li><a href='/nlp_form/alice'>/nlp_form/alice</a></li>"
        "<li><a href='/nlp_form/bob'>/nlp_form/bob</a></li>"
        "<li><a href='/routes'>/routes</a></li>"
        "</ul>"
        f"<p>BASE_URL: {BASE_URL}</p>"
    )

@app.route("/routes")
def show_routes():
    return "<br>".join(sorted(rule.rule for rule in app.url_map.iter_rules()))

@app.route("/debug/env")
def debug_env():
    cfg = os.environ.get("GOOGLE_CLIENT_CONFIG_JSON", "")
    preview = (cfg[:150] + "...") if cfg else "(missing)"
    return (
        f"BASE_URL = {BASE_URL}<br>"
        f"APP_SECRET set = {bool(os.environ.get('APP_SECRET'))}<br>"
        f"GOOGLE_CLIENT_CONFIG_JSON = {'set' if cfg else 'MISSING'}<br>"
        f"OPENAI_API_KEY set = {bool(os.environ.get('OPENAI_API_KEY'))}<br>"
        f"preview = {preview}"
    )

@app.route("/debug/redirect/<user>")
def debug_redirect(user):
    return f"redirect_uri = {BASE_URL}/oauth2/callback/{user}"

# ─────────────────────────────
# OAuth: 사용자별 (alice/bob) 로그인 플로우
# ─────────────────────────────
@app.route("/auth/<user>")
def auth_user(user):
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = f"{BASE_URL}/oauth2/callback/{user}"
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    session["state"] = state
    return redirect(auth_url)

@app.route("/oauth2/callback/<user>")
def oauth2_callback_user(user):
    # 콜백 URL을 직접 열었거나 state 유실 시 /auth로 유도
    if not request.args.get("state"):
        return redirect(f"/auth/{user}")
    state = session.get("state")
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=SCOPES, state=state)
    flow.redirect_uri = f"{BASE_URL}/oauth2/callback/{user}"
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    save_credentials_for(user, creds)
    return f"✅ {user} 연결 완료! <a href='/'>홈</a>"

# state 불일치 시 부드럽게 복구
@app.errorhandler(MismatchingStateError)
def handle_state_error(e):
    return redirect("/"), 302

# ─────────────────────────────
# 캘린더: 확인 & 테스트 이벤트 생성
# ─────────────────────────────
@app.route("/whoami/<user>")
def whoami(user):
    creds = load_credentials_for(user)
    if not creds:
        return f"{user} 토큰 없음. 먼저 /auth/{user} 로 로그인하세요."
    service = build_service(creds)
    info = service.calendars().get(calendarId="primary").execute()
    return f"{user} 캘린더: {info.get('summary')}"

@app.route("/make_test_event")
def make_test_event():
    # 단일 사용자(me)를 쓰고 싶으면 /auth/me 라우트를 추가해도 됨
    return "이 데모는 alice/bob 하드코딩 버전을 사용합니다. /auth/alice 또는 /auth/bob 후 테스트하세요."

@app.route("/make_test_event_multi")
def make_test_event_multi():
    users = ["alice", "bob"]  # 하드코딩: 두 사람 각자 캘린더에 동일 이벤트 생성
    start = datetime.now(KST).replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
    end   = start + timedelta(hours=1)
    attendees = normalize_attendees(["alice", "bob"])  # 서로를 초대

    results = []
    for user in users:
        creds = load_credentials_for(user)
        if not creds:
            results.append(f"❌ {user}: 먼저 /auth/{user} 로 로그인하세요.")
            continue
        service = build_service(creds)
        event = {
            "summary": "멀티 테스트 회의",
            "start": {"dateTime": start.isoformat()},
            "end":   {"dateTime": end.isoformat()},
            "attendees": attendees,
        }
        created = service.events().insert(
            calendarId="primary", body=event, sendUpdates="all"
        ).execute()
        results.append(f"✅ {user}: <a href='{created.get('htmlLink')}' target='_blank'>생성됨</a>")
    return "<br>".join(results)

# ─────────────────────────────
# 자연어 입력 폼 & 처리 (GPT → 캘린더)
# ─────────────────────────────
@app.route("/nlp_form/<user>")
def nlp_form(user):
    return f"""
    <h3>NLP Event Creator (작성자: {user})</h3>
    <form method="POST" action="/nlp_event/{user}">
      <input type="text" name="text" placeholder="예: 오늘 오후 1시~3시 엘리스랑 회의" style="width:360px">
      <button type="submit">등록</button>
    </form>
    """

@app.route("/nlp_event/<user>", methods=["POST"])
def nlp_event(user):
    # 1) 자격 확인
    creds = load_credentials_for(user)
    if not creds:
        return f"{user} 먼저 /auth/{user} 로 로그인하세요."

    text = request.form.get("text", "").strip()
    if not text:
        return "❌ text 파라미터가 비었습니다."

    # 2) OpenAI 키 존재 확인
    if not os.environ.get("OPENAI_API_KEY"):
        return "❌ 서버에 OPENAI_API_KEY가 설정되어 있지 않습니다. (Render Environment 확인)"

    # 3) 자연어 → JSON
    parsed = nlp_to_event_json(text)
    if "error" in parsed:
        return f"❌ {parsed['error']}"

    summary = parsed.get("summary") or "회의"
    start_iso = parsed.get("start")
    end_iso   = parsed.get("end")
    attendees_raw = parsed.get("attendees") or []

    if not (start_iso and end_iso):
        return "❌ 시간 파싱 실패. 예: '오늘 오후 1시~3시 회의' 처럼 다시 입력해 주세요."

    # 4) 이벤트 생성
    service = build_service(creds)
    event = {
        "summary": summary,
        "start": {"dateTime": start_iso},
        "end":   {"dateTime": end_iso},
        "attendees": normalize_attendees(attendees_raw),
    }
    try:
        created = service.events().insert(
            calendarId="primary", body=event, sendUpdates="all"
        ).execute()
    except Exception as ge:
        return f"❌ 구글 캘린더 생성 실패: {ge}"

    return f"✅ 이벤트 생성 완료! <a href='{created.get('htmlLink')}' target='_blank'>구글 캘린더 확인</a>"

# ─────────────────────────────
# 엔트리포인트
# ─────────────────────────────
if __name__ == "__main__":
    print(">>> FLASK APP:", __name__)
    app.run(port=5000, debug=True)
