import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Shanghai")


def now():
    return datetime.now(ZONE).isoformat()


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,title TEXT,course_id TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,metadata TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS practices(id TEXT PRIMARY KEY,payload TEXT,result TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,topic TEXT,course_id TEXT,source_ids TEXT,misconception TEXT,stage INTEGER,due_at TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS traces(id TEXT PRIMARY KEY,payload TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS usage(month TEXT PRIMARY KEY,search_count INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS context_boundary(id INTEGER PRIMARY KEY,changed_at TEXT);
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def session(self, session_id, course_id, title):
        with self.connect() as c:
            if session_id:
                row = c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
                if not row:
                    raise ValueError("会话不存在，请新建会话。")
                if row["course_id"] != course_id:
                    raise ValueError("课程范围已改变，请新建会话。")
                return session_id
            session_id = uid()
            c.execute("INSERT INTO sessions VALUES (?,?,?,?)", (session_id, title[:40], course_id, now()))
            return session_id

    def add_message(self, session_id, role, content, metadata=None):
        with self.connect() as c:
            c.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?)",
                (
                    uid(),
                    session_id,
                    role,
                    content,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now(),
                ),
            )

    def messages(self, session_id):
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY created_at,rowid", (session_id,)
            ).fetchall()
        return [{**dict(r), "metadata": json.loads(r["metadata"])} for r in rows]

    def sessions(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM sessions ORDER BY created_at DESC")]

    def delete_session(self, session_id):
        with self.connect() as c:
            c.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            c.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            # Traces may contain a derived answer; clear them together with history deletion.
            c.execute("DELETE FROM traces")

    def preferences(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM preferences ORDER BY key")]

    def preference(self, key, value):
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO preferences VALUES (?,?,?)", (key, value, now()))
            c.execute("INSERT OR REPLACE INTO context_boundary VALUES (1,?)", (now(),))

    def delete_preference(self, key):
        with self.connect() as c:
            c.execute("DELETE FROM preferences WHERE key=?", (key,))
            c.execute("INSERT OR REPLACE INTO context_boundary VALUES (1,?)", (now(),))

    def context_messages(self, session_id):
        with self.connect() as c:
            boundary = c.execute("SELECT changed_at FROM context_boundary WHERE id=1").fetchone()
        return [m for m in self.messages(session_id) if not boundary or m["created_at"] > boundary[0]]

    def practice(self, practice_id):
        with self.connect() as c:
            r = c.execute("SELECT * FROM practices WHERE id=?", (practice_id,)).fetchone()
        if not r:
            raise ValueError("练习不存在。")
        return {
            "id": r["id"],
            **json.loads(r["payload"]),
            "result": json.loads(r["result"]) if r["result"] else None,
            "created_at": r["created_at"],
        }

    def add_practice(self, payload):
        practice_id = uid()
        with self.connect() as c:
            c.execute(
                "INSERT INTO practices VALUES (?,?,NULL,?)",
                (practice_id, json.dumps(payload, ensure_ascii=False), now()),
            )
        return self.practice(practice_id)

    def practices(self):
        with self.connect() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM practices ORDER BY created_at DESC")]
        return [self.practice(i) for i in ids]

    def finish_practice(self, practice_id, result):
        practice = self.practice(practice_id)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute("SELECT result FROM practices WHERE id=?", (practice_id,)).fetchone()
            if existing[0]:
                return json.loads(existing[0])
            c.execute(
                "UPDATE practices SET result=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), practice_id),
            )
            grouped = {}
            for question, grade in zip(practice["questions"], result["grades"], strict=True):
                topic = practice.get("review_topic") or question["topic"]
                score = grade["correctness"] + grade["completeness"] + grade["reasoning"]
                rank = (grade["correctness"] == 2 and score >= 5, score)
                if topic not in grouped or rank < grouped[topic][0]:
                    grouped[topic] = (rank, question, grade)
            for _, question, grade in grouped.values():
                topic = practice.get("review_topic") or question["topic"]
                key = f"{practice.get('course_id') or 'all'}:{topic}"
                old = c.execute("SELECT * FROM reviews WHERE id=?", (key,)).fetchone()
                score = grade["correctness"] + grade["completeness"] + grade["reasoning"]
                passed = score >= 5 and grade["correctness"] == 2
                if not old and passed:
                    continue
                # Advance a topic at most once per calendar day; first failure starts at day 1.
                already_today = old and old["updated_at"][:10] == now()[:10]
                stage = min(old["stage"] + (0 if already_today else 1), 2) if old and passed else 0
                due = (datetime.now(ZONE) + timedelta(days=[1, 3, 7][stage])).isoformat()
                c.execute(
                    "INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?,?,?,?)",
                    (
                        key,
                        topic,
                        practice.get("course_id"),
                        json.dumps(question["source_ids"]),
                        grade["misconception"] or grade["missing"],
                        stage,
                        due,
                        now(),
                    ),
                )
        return result

    def reviews(self):
        with self.connect() as c:
            return [
                {**dict(r), "source_ids": json.loads(r["source_ids"]), "due": r["due_at"] <= now()}
                for r in c.execute("SELECT * FROM reviews ORDER BY due_at")
            ]

    def clear_learning(self):
        with self.connect() as c:
            for table in ("practices", "reviews", "traces"):
                c.execute(f"DELETE FROM {table}")
            c.execute("INSERT OR REPLACE INTO context_boundary VALUES (1,?)", (now(),))

    def trace(self, run):
        with self.connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO traces VALUES (?,?,?)",
                (run["id"], json.dumps(run, ensure_ascii=False), now()),
            )

    def traces(self):
        with self.connect() as c:
            return [
                json.loads(r[0])
                for r in c.execute("SELECT payload FROM traces ORDER BY created_at DESC LIMIT 50")
            ]

    def reserve_search(self, limit):
        month = datetime.now(ZONE).strftime("%Y-%m")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO usage VALUES (?,0)", (month,))
            count = c.execute("SELECT search_count FROM usage WHERE month=?", (month,)).fetchone()[0]
            if count >= limit:
                raise ValueError("本项目本月的免费搜索保护额度已用完。")
            c.execute("UPDATE usage SET search_count=search_count+1 WHERE month=?", (month,))
        return count + 1

    def search_usage(self):
        with self.connect() as c:
            r = c.execute(
                "SELECT search_count FROM usage WHERE month=?", (datetime.now(ZONE).strftime("%Y-%m"),)
            ).fetchone()
        return r[0] if r else 0
