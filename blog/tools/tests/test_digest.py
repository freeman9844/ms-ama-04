import json
import os
import sqlite3
import subprocess

from blog.tools.digest import (
    Item,
    kst_date,
    main,
    read_checkpoints,
    read_deploy_history,
    read_events,
    read_git,
    write_digests,
)

EVENTS = [
    {"type": "user.message", "timestamp": "2026-10-31T00:00:00Z", "data": {"content": ""}},
    {"type": "user.message", "timestamp": "2026-10-31T00:00:01Z", "data": {"content": "hello"}},
    {"type": "assistant.turn_start", "timestamp": "2026-10-31T00:00:02Z", "data": {"turnId": "0"}},
    {
        "type": "human_response.recorded",
        "timestamp": "2026-10-31T00:00:03Z",
        "data": {
            "response": {
                "responseKind": "ask_user",
                "message": "어느 쪽?",
                "content": {"choice": "A", "note": "ok"},
                "requestedSchema": {},
            }
        },
    },
    {
        "type": "tool.execution_complete",
        "timestamp": "2026-10-31T00:00:04Z",
        "data": {"success": "False", "error": {"message": "boom"}},
    },
    {"type": "tool.execution_complete", "timestamp": "2026-10-31T00:00:05Z", "data": {"success": True}},
    {"type": "assistant.message", "timestamp": "2026-10-31T00:00:06Z", "data": {"content": "first reply", "turnId": "0"}},
    {"type": "assistant.message", "timestamp": "2026-10-31T00:00:07Z", "data": {"content": "second reply", "turnId": "0"}},
    {"type": "assistant.turn_end", "timestamp": "2026-10-31T00:00:08Z", "data": {"turnId": "0"}},
]


def write_jsonl(tmp_path, events):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    return path


def test_utc_evening_event_goes_to_next_kst_day():
    assert kst_date("2026-10-31T15:30:00Z") == "2026-11-01"
    assert kst_date("2026-10-31T14:59:59.000Z") == "2026-10-31"
    assert kst_date("2026-10-26T18:45:56.381+09:00") == "2026-10-26"


def test_read_events_extracts_user_ask_and_failures(tmp_path):
    items = read_events(write_jsonl(tmp_path, EVENTS), "A1")
    assert [i.kind for i in items] == ["user", "ask", "answer", "tool_fail", "assistant"]
    assert items[-1].text == "second reply"
    assert all(i.source == "session:A1" for i in items)
    assert items[0].text == "hello"
    assert items[0].day == "2026-10-31" and items[0].time == "09:00"
    assert items[1].text == "어느 쪽?"
    assert items[2].text == "choice=A; note=ok"
    assert items[3].text == "boom"


def test_read_events_keeps_last_reply_per_interaction_and_truncates(tmp_path):
    long = "x" * 700
    events = [
        {"type": "user.message", "timestamp": "2026-10-31T00:00:00Z", "data": {"content": "q1"}},
        {"type": "assistant.message", "timestamp": "2026-10-31T00:00:01Z",
         "data": {"content": "a1-mid", "turnId": "0", "interactionId": "i1"}},
        {"type": "assistant.message", "timestamp": "2026-10-31T00:00:02Z",
         "data": {"content": long, "turnId": "1", "interactionId": "i1"}},
        {"type": "assistant.message", "timestamp": "2026-10-31T00:00:03Z",
         "data": {"content": "", "turnId": "2", "interactionId": "i1"}},
        {"type": "user.message", "timestamp": "2026-10-31T00:01:00Z", "data": {"content": "q2"}},
        {"type": "assistant.message", "timestamp": "2026-10-31T00:01:01Z",
         "data": {"content": "a2", "turnId": "0", "interactionId": "i2"}},
    ]
    items = read_events(write_jsonl(tmp_path, events), "A1")
    assert [(i.kind, i.text[:6]) for i in items] == [
        ("user", "q1"), ("assistant", "xxxxxx"), ("user", "q2"), ("assistant", "a2"),
    ]
    assert len(items[1].text) == 600


def test_read_events_accepts_stringified_data(tmp_path):
    events = [{"type": "user.message", "timestamp": "2026-10-31T00:00:00Z",
               "data": json.dumps({"content": "hi"})}]
    assert [i.text for i in read_events(write_jsonl(tmp_path, events), "A1")] == ["hi"]


def test_read_checkpoints_reads_title_and_overview(tmp_path):
    db = tmp_path / "store.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE checkpoints(id INTEGER PRIMARY KEY, session_id TEXT, checkpoint_number INTEGER,"
        " title TEXT, overview TEXT, history TEXT, work_done TEXT, technical_details TEXT,"
        " important_files TEXT, next_steps TEXT, created_at TEXT)"
    )
    con.execute(
        "INSERT INTO checkpoints(session_id, checkpoint_number, title, overview, created_at)"
        " VALUES ('s1', 1, '제목', '개요', '2026-10-31 15:30:00')"
    )
    con.execute(
        "INSERT INTO checkpoints(session_id, checkpoint_number, title, overview, created_at)"
        " VALUES ('other', 1, 'nope', 'nope', '2026-10-31 15:30:00')"
    )
    con.commit()
    con.close()
    items = read_checkpoints(db, "s1", "A1")
    assert items == [Item("2026-11-01", "00:30", "session:A1", "checkpoint", "제목\n개요")]


def test_read_git_parses_tab_separated_log(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(git + ["init", "-q"], check=True)
    env_date = "2026-10-31T15:30:00+00:00"
    subprocess.run(
        git + ["commit", "-q", "--allow-empty", "-m", "feat: 첫\t커밋", "--date", env_date],
        check=True,
        env={**os.environ, "GIT_COMMITTER_DATE": env_date},
    )
    items = read_git(repo, "A1")
    assert items == [Item("2026-11-01", "00:30", "git:A1", "commit", "feat: 첫\t커밋")]


def test_read_deploy_history_splits_sections(tmp_path):
    md = tmp_path / "deploy-history.md"
    md.write_text(
        "# 배포 이력\n\n소개\n\n"
        "## 2026-11-17 전체 소스 리뷰 반영 배포\n\n- 항목 1\n- 항목 2\n\n"
        "## 2026-11-10 첫 배포\n- 하나\n",
        encoding="utf-8",
    )
    items = read_deploy_history(md)
    assert [(i.day, i.source, i.kind) for i in items] == [
        ("2026-11-17", "deploy-history", "deploy"),
        ("2026-11-10", "deploy-history", "deploy"),
    ]
    assert items[0].text == "전체 소스 리뷰 반영 배포\n- 항목 1\n- 항목 2"
    assert items[1].text == "첫 배포\n- 하나"


def test_write_digests_groups_by_day_in_time_order(tmp_path):
    paths = write_digests([Item("2026-11-01", "10:00", "git:A", "commit", "b"),
                           Item("2026-10-31", "09:00", "git:A", "commit", "a"),
                           Item("2026-11-01", "08:00", "session:A", "user", "q")], tmp_path)
    assert [p.name for p in paths] == ["2026-10-31.md", "2026-11-01.md"]
    body = paths[1].read_text(encoding="utf-8")
    assert body.index("q") < body.index("b")
    assert "사용자 메시지 1" in body and "실패 0" in body and "커밋 1" in body
    assert "### session:A" in body and "### git:A" in body


def test_cli_builds_digests_from_sources_json(tmp_path):
    state = tmp_path / "state"
    (state / "s1").mkdir(parents=True)
    write_jsonl(state / "s1", EVENTS[:2])
    md = tmp_path / "deploy-history.md"
    md.write_text("## 2026-10-31 배포\n- 내용\n", encoding="utf-8")
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps({
        "sources": [{"label": "A1", "session_id": "s1"}],
        "deploy_history": str(md),
        "session_db": str(tmp_path / "missing.db"),
        "session_state": str(state),
    }), encoding="utf-8")
    out = tmp_path / "out"
    assert main(["--sources", str(sources), "--out", str(out)]) == 0
    body = (out / "2026-10-31.md").read_text(encoding="utf-8")
    assert "hello" in body and "배포" in body
