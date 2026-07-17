"""Persisted BYOK model-id migration tests."""
from sqlalchemy import create_engine, text

from auto_dm.web.server import _migrate_llm_model_ids


def test_migrate_llm_model_ids_is_complete_and_idempotent():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE user_llm_settings ("
            "user_id INTEGER PRIMARY KEY, provider VARCHAR(32), model VARCHAR(128))"
        )
        old_rows = [
            (1, "openai", "gpt-5-mini"),
            (2, "openai", "gpt-5.1"),
            (3, "gemini", "gemini-2.5-flash"),
            (4, "gemini", "gemini-2.5-pro"),
            (5, "deepseek", "deepseek-chat"),
            (6, "deepseek", "deepseek-reasoner"),
            (7, "anthropic", "claude-sonnet-5"),
        ]
        for row in old_rows:
            conn.execute(
                text(
                    "INSERT INTO user_llm_settings (user_id, provider, model) "
                    "VALUES (:user_id, :provider, :model)"
                ),
                {"user_id": row[0], "provider": row[1], "model": row[2]},
            )

        _migrate_llm_model_ids(conn)
        _migrate_llm_model_ids(conn)
        rows = conn.execute(
            text("SELECT user_id, model FROM user_llm_settings ORDER BY user_id")
        ).all()

    assert rows == [
        (1, "gpt-5.4-mini"),
        (2, "gpt-5.4"),
        (3, "gemini-3.5-flash"),
        (4, "gemini-3.5-flash"),
        (5, "deepseek-v4-flash"),
        (6, "deepseek-v4-flash"),
        (7, "claude-sonnet-5"),
    ]
