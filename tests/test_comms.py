from loompa.comms import (
    FounderMessage,
    MessageKind,
    Option,
    audit_executive_text,
    compose_blocked_message,
    compose_delivery_message,
    sanitize_for_founder,
)

TRACE = """Tests failed while saving invoices.
Traceback (most recent call last):
  File "/Users/dev/app/src/billing/service.py", line 42, in save
    db.commit()
sqlalchemy.exc.IntegrityError: duplicate key value violates unique constraint
src/billing/service.py:42 raised the error at 0xdeadbeefcafe
"""


def test_sanitize_removes_traces_paths_and_jargon():
    out = sanitize_for_founder(TRACE)
    assert "Traceback" not in out
    assert 'File "' not in out
    assert "/Users/dev" not in out
    assert "IntegrityError" not in out
    assert "0xdeadbeef" not in out
    assert "service.py:42" not in out
    assert "Tests failed while saving invoices." in out


def test_sanitize_truncates_long_text():
    out = sanitize_for_founder("palavra " * 1000, max_chars=200)
    assert len(out) <= 200 and out.endswith("…")


def test_audit_flags_technical_noise_and_passes_clean_text():
    violations = audit_executive_text(TRACE)
    rules = {v.rule for v in violations}
    assert {
        "stack_trace",
        "raw_exception",
        "file_line_ref",
        "absolute_path",
        "hex_identifier",
    } <= rules
    assert (
        audit_executive_text("Precisamos decidir se o usuário recupera a senha por e-mail ou SMS.")
        == []
    )


def test_blocked_message_is_executive_and_has_default_options():
    msg = compose_blocked_message(
        factory="saas",
        story_id="S-1",
        story_title="Recuperação de senha",
        reason=TRACE,
        technical_ref="logs/S-1.log",
    )
    assert msg.kind == MessageKind.BLOCKED
    assert msg.requires_action
    assert msg.executive_audit() == []
    assert [o.key for o in msg.options] == ["retry", "skip", "drop"]
    assert msg.options[0].recommended
    assert msg.technical_ref == "logs/S-1.log"


def test_delivery_message_includes_cost_and_pr():
    msg = compose_delivery_message(
        factory="saas",
        story_id="S-2",
        story_title="Login",
        summary="Tela de login pronta.",
        pr_url="https://x/pr/1",
        cost_usd=0.4321,
    )
    assert "US$ 0.43" in msg.impact and "https://x/pr/1" in msg.impact
    assert not msg.requires_action  # deliveries are informational until founder acts


def test_message_serialisation_roundtrip():
    msg = FounderMessage(title="t", context="c", options=[Option(key="a", label="A")])
    again = FounderMessage.model_validate_json(msg.model_dump_json())
    assert again.id == msg.id and again.options[0].label == "A"
