from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import AsyncGroq
import sqlite3, json, os, re
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect("career.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            career_goal TEXT NOT NULL,
            known_skills TEXT DEFAULT '[]',
            skill_level TEXT DEFAULT 'beginner',
            gaps TEXT DEFAULT '[]',
            summary TEXT DEFAULT '',
            assessment_done INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS roadmap_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT,
            description TEXT,
            duration TEXT,
            order_num INTEGER,
            status TEXT DEFAULT 'locked',
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS quiz_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            topic_id INTEGER,
            score INTEGER,
            total INTEGER,
            completed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (topic_id) REFERENCES roadmap_topics(id)
        );
    """)
    conn.commit()
    conn.close()

init_db()


# ── Models ────────────────────────────────────────────────────────────────────

class CreateUser(BaseModel):
    name: str
    career_goal: str

class ChatMessage(BaseModel):
    user_id: int
    message: str

class GenerateRoadmap(BaseModel):
    user_id: int

class GenerateQuiz(BaseModel):
    user_id: int
    topic_id: int

class SubmitQuiz(BaseModel):
    user_id: int
    topic_id: int
    score: int
    total: int


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def serve_app():
    return FileResponse("static/index.html")

@app.post("/api/users")
async def create_user(data: CreateUser):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO users (name, career_goal) VALUES (?, ?)",
        (data.name.strip(), data.career_goal.strip())
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"user_id": user_id, "name": data.name, "career_goal": data.career_goal}


@app.post("/api/chat")
async def chat(data: ChatMessage):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (data.user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(404, "User not found")

    history = conn.execute(
        "SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id",
        (data.user_id,)
    ).fetchall()

    messages = [{"role": row["role"], "content": row["content"]} for row in history]
    messages.append({"role": "user", "content": data.message})

    system = f"""You are CareerAI, a friendly career counselor for students.
The student's name is {user['name']} and they want to become: {user['career_goal']}

Your job: assess their current knowledge through a natural conversation.
Ask ONE short question at a time about:
- Programming experience & background
- Languages, tools, frameworks they know
- Projects they've worked on
- How long they've been learning

Keep each response SHORT (2-3 sentences max + 1 question). Be warm and encouraging.

After 4-6 exchanges when you have enough info, output ONLY this JSON (nothing else, no text before or after):
{{
  "assessment_complete": true,
  "known_skills": ["skill1", "skill2"],
  "skill_level": "beginner",
  "gaps": ["gap1", "gap2"],
  "summary": "one clear sentence about their current level"
}}

skill_level must be exactly: beginner, intermediate, or advanced"""

    response = await client.chat.completions.create(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "system", "content": system}, *messages],
    )

    reply = response.choices[0].message.content

    conn.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)",
                 (data.user_id, "user", data.message))
    conn.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)",
                 (data.user_id, "assistant", reply))

    assessment = None
    try:
        json_match = re.search(r'\{[\s\S]*"assessment_complete"[\s\S]*\}', reply)
        if json_match:
            assessment = json.loads(json_match.group())
            conn.execute("""UPDATE users SET
                known_skills=?, skill_level=?, gaps=?, summary=?, assessment_done=1
                WHERE id=?""", (
                json.dumps(assessment.get("known_skills", [])),
                assessment.get("skill_level", "beginner"),
                json.dumps(assessment.get("gaps", [])),
                assessment.get("summary", ""),
                data.user_id
            ))
    except Exception:
        pass

    conn.commit()
    conn.close()
    return {"reply": reply, "assessment": assessment}


@app.post("/api/roadmap/generate")
async def generate_roadmap(data: GenerateRoadmap):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (data.user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(404, "User not found")

    existing = conn.execute("SELECT id FROM roadmap_topics WHERE user_id = ?", (data.user_id,)).fetchall()
    if existing:
        topics = conn.execute(
            "SELECT * FROM roadmap_topics WHERE user_id = ? ORDER BY order_num", (data.user_id,)
        ).fetchall()
        conn.close()
        return {"topics": [dict(t) for t in topics]}

    prompt = f"""Create a personalized learning roadmap for:
- Name: {user['name']}
- Career Goal: {user['career_goal']}
- Current Level: {user['skill_level']}
- Known Skills: {user['known_skills']}
- Skill Gaps: {user['gaps']}
- Their Background: {user['summary']}

Return ONLY valid JSON, no other text:
{{
  "topics": [
    {{
      "title": "Topic Name",
      "description": "What they will learn in 1-2 sentences",
      "duration": "1-2 weeks",
      "order": 1
    }}
  ]
}}

Rules:
- Include 7-10 topics total
- Start from their current level, skip what they clearly know
- Order from foundational to advanced
- Be very specific to {user['career_goal']}
- First topic should always be unlocked/available"""

    response = await client.chat.completions.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.choices[0].message.content
    json_match = re.search(r'\{[\s\S]*\}', raw)
    if not json_match:
        conn.close()
        raise HTTPException(500, "Failed to parse roadmap")

    roadmap = json.loads(json_match.group())
    topics = roadmap.get("topics", [])

    for i, topic in enumerate(topics):
        status = "available" if i == 0 else "locked"
        conn.execute(
            "INSERT INTO roadmap_topics (user_id, title, description, duration, order_num, status) VALUES (?,?,?,?,?,?)",
            (data.user_id, topic["title"], topic["description"], topic.get("duration","1-2 weeks"), i+1, status)
        )

    conn.commit()
    saved = conn.execute(
        "SELECT * FROM roadmap_topics WHERE user_id = ? ORDER BY order_num", (data.user_id,)
    ).fetchall()
    conn.close()
    return {"topics": [dict(t) for t in saved]}


@app.get("/api/roadmap/{user_id}")
async def get_roadmap(user_id: int):
    conn = get_db()
    topics = conn.execute(
        "SELECT * FROM roadmap_topics WHERE user_id = ? ORDER BY order_num", (user_id,)
    ).fetchall()
    conn.close()
    return {"topics": [dict(t) for t in topics]}


@app.post("/api/quiz/generate")
async def generate_quiz(data: GenerateQuiz):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (data.user_id,)).fetchone()
    topic = conn.execute("SELECT * FROM roadmap_topics WHERE id = ?", (data.topic_id,)).fetchone()
    conn.close()

    if not user or not topic:
        raise HTTPException(404, "Not found")

    prompt = f"""Generate 5 multiple choice questions to assess knowledge of: "{topic['title']}"
Context: {topic['description']}
For someone learning to become a: {user['career_goal']}
Student level: {user['skill_level']}

Return ONLY valid JSON:
{{
  "questions": [
    {{
      "q": "Question text?",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct": 0,
      "explanation": "Why this is correct in one sentence."
    }}
  ]
}}

correct is 0-indexed (0=A, 1=B, 2=C, 3=D). Make questions practical and code-relevant."""

    response = await client.chat.completions.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.choices[0].message.content
    json_match = re.search(r'\{[\s\S]*\}', raw)
    if not json_match:
        raise HTTPException(500, "Failed to parse quiz")

    quiz = json.loads(json_match.group())
    return quiz


@app.post("/api/quiz/submit")
async def submit_quiz(data: SubmitQuiz):
    conn = get_db()

    conn.execute(
        "INSERT INTO quiz_results (user_id, topic_id, score, total) VALUES (?,?,?,?)",
        (data.user_id, data.topic_id, data.score, data.total)
    )

    passed = (data.score / data.total) >= 0.6
    if passed:
        conn.execute("UPDATE roadmap_topics SET status='completed' WHERE id=?", (data.topic_id,))

        current = conn.execute(
            "SELECT order_num FROM roadmap_topics WHERE id=?", (data.topic_id,)
        ).fetchone()

        if current:
            conn.execute("""UPDATE roadmap_topics SET status='available'
                WHERE user_id=? AND order_num=? AND status='locked'""",
                (data.user_id, current["order_num"] + 1))

    conn.commit()
    conn.close()
    return {"passed": passed, "score": data.score, "total": data.total}


@app.get("/api/progress/{user_id}")
async def get_progress(user_id: int):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    topics = conn.execute(
        "SELECT * FROM roadmap_topics WHERE user_id=? ORDER BY order_num", (user_id,)
    ).fetchall()
    results = conn.execute(
        "SELECT qr.*, rt.title FROM quiz_results qr JOIN roadmap_topics rt ON qr.topic_id=rt.id WHERE qr.user_id=? ORDER BY qr.completed_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()

    total = len(topics)
    completed = sum(1 for t in topics if t["status"] == "completed")
    avg_score = 0
    if results:
        avg_score = round(sum(r["score"] / r["total"] * 100 for r in results) / len(results))

    return {
        "user": dict(user) if user else {},
        "topics": [dict(t) for t in topics],
        "results": [dict(r) for r in results],
        "stats": {
            "total_topics": total,
            "completed": completed,
            "percent": round(completed / total * 100) if total else 0,
            "avg_score": avg_score,
            "quizzes_taken": len(results),
        }
    }


app.mount("/static", StaticFiles(directory="static"), name="static")
