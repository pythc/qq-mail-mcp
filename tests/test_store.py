from concurrent.futures import ThreadPoolExecutor

from qq_mail_mcp.store import Store


def test_sensitive_data_is_encrypted_and_tokens_not_plaintext(settings):
    store = Store(settings)
    store.put("mailbox", "owner", {"auth_code": "never-plaintext-auth-code"})
    store.put("access", "never-plaintext-token", {"subject": "owner"})
    raw = settings.database.read_bytes()
    assert b"never-plaintext-auth-code" not in raw
    assert b"never-plaintext-token" not in raw
    assert store.get("mailbox", "owner")["auth_code"] == "never-plaintext-auth-code"


def test_atomic_consume(settings):
    store = Store(settings)
    store.put("code", "example", {"a": 1})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.get("code", "example", consume=True), range(4)))
    assert sum(value is not None for value in results) == 1


def test_draft_approval_and_submission_are_single_use_and_client_bound(settings):
    store = Store(settings)
    draft_id = store.new_draft("client-a", {"subject": "固定主题"})
    assert store.draft(draft_id, "client-b") is None
    assert not store.transition(draft_id, "approved", "sending", "client-a")
    assert store.transition(draft_id, "pending", "approved")
    assert not store.transition(draft_id, "approved", "sending", "client-b")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: store.transition(draft_id, "approved", "sending", "client-a"), range(4)
            )
        )
    assert sum(results) == 1
