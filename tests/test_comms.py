from loompa.comms import (
    FounderMessage,
    MessageKind,
    Option,
    audit_executive_text,
    compose_blocked_message,
    compose_delivery_message,
    sanitize_for_founder,
)
from test_engine import factory, make_ctx  # noqa: F401

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


def test_audit_flags_project_files_and_git_commands_in_option_labels():
    """`contas` Sprint 2: a founder option said "git checkout -- src/contas/cli.py"; the audit
    only knew absolute paths and `file:line`, and it never read option labels."""
    leaked = "Reverter você mesmo com git: git checkout -- src/contas/cli.py"
    assert {v.rule for v in audit_executive_text(leaked)} == {"file_name", "git_command"}
    clean = sanitize_for_founder(leaked)
    assert "cli.py" not in clean and "git checkout" not in clean
    # product words that look like files stay: money, domains, the CSV the founder exports
    assert audit_executive_text("R$ 12.50 virou 1250 centavos em contas.app.br e gastos.csv") == []
    msg = compose_blocked_message(
        factory="contas",
        story_id="S-1",
        story_title="Centavos",
        reason="Falta uma decisão.",
        options=[Option(key="opt1", label=leaked)],
    )
    assert {v.rule for v in msg.executive_audit()} == {"file_name", "git_command"}


def test_a_replaced_file_name_does_not_double_its_noun_and_code_names_do_not_pass():
    """SP-010 (06/10): "o arquivo um arquivo do projeto ... (FAIXA_RETORNO_Y, linhas 1959–1963)"
    reached the founder."""
    from loompa.comms.executive import audit_executive_text, sanitize_for_founder

    raw = (
        "Mas o arquivo tests/casca.test.js fixa a banda antiga numa constante "
        "(FAIXA_RETORNO_Y, linhas 1959–1963) e o campo valor_centavos também."
    )
    out = sanitize_for_founder(raw)
    assert "arquivo um arquivo" not in out and "mas um arquivo do projeto" in out.lower()
    assert "FAIXA_RETORNO_Y" not in out and "1959" not in out and "valor_centavos" not in out
    assert not audit_executive_text(out)
    rules = {v.rule for v in audit_executive_text(raw)}
    assert {"code_name", "line_ref"} <= rules
    assert sanitize_for_founder("Está no arquivo src/app.py.") == "Está num arquivo do projeto."
    plain = "Vale testar no celular: o pet atravessa 8 horas sem fim de jogo."
    assert sanitize_for_founder(plain) == plain and not audit_executive_text(plain)


async def test_a_rewrite_with_a_task_number_is_cleaned_and_kept_not_thrown_away(factory):  # noqa: F811
    """14.3: 10 of 36 executive rewrites named "T1" or a git word, failed the audit and were
    replaced by the team's own text. The deterministic filter now runs first, as on the fallback."""
    import json

    from loompa.agents import MasterAgent
    from loompa.engine.state import StoryState

    def script(model, messages, tools):
        if "Rewrite the technical problem" in messages[0].content:
            return json.dumps(
                {
                    "title": "A T1 parou num teste antigo",
                    "context": "Ao fazer a T1, o checklist mostrou dois testes de outra história.",
                    "impact": "A entrega espera a sua escolha.",
                    "options": ["Ajustar os testes", "Deixar para depois"],
                }
            )
        return "{}"

    master = MasterAgent(make_ctx(factory, script))
    msg = await master.blocked_message(
        StoryState(story_id="S-001", title="Fim de jogo"),
        "T1 failed: tests/test_idade.py",
        options=["Ajustar tests/test_idade.py", "Deixar para depois"],
    )
    assert msg.title.startswith("A tarefa 1 parou")  # the model's rewrite, cleaned
    assert "roteiro de verificação" in msg.context and "T1" not in msg.context
    # "Deixar para depois" is the way out already, read by its words; "cancel" joins it
    assert [o.label for o in msg.options] == [
        "Ajustar os testes",
        "Deixar para depois",
        "Cancelar esta entrega",
    ]
    assert [o.key for o in msg.options] == ["opt1", "skip", "drop"]
    assert not audit_executive_text(f"{msg.title}\n{msg.context}\n{msg.impact}")


def test_names_side_by_side_read_as_plain_portuguese():
    """ADR-0036 §8: real founder text from tamagotchi deliveries, as the sanitizer left it."""
    from loompa.comms.executive import sanitize_for_founder

    cases = {
        "Quebrar o ciclo de imports entre src/sprites.js e src/lcd_screen.js": (
            "Quebrar o ciclo de imports entre dois arquivos do projeto"
        ),
        "Adicionar teste de regressão para content-types do servidor": (
            "Adicionar teste contra a volta de defeitos para content-types do servidor"
        ),
        "detectar regressão real no carregamento": "detectar volta de um defeito real no carregamento",
        "a navegação para o start_url './' resulta": "a navegação para um valor do código './' resulta",
        "[Oportunidade] sw.js: o pré-cache não inclui a página": (
            "[Oportunidade] o pré-cache não inclui a página"
        ),
        "mas tests/casca.test.js () fixa a lista": "mas um arquivo do projeto fixa a lista",
        "Chegamos a uma decisão.": "Chegamos a uma decisão.",
    }
    for raw, plain in cases.items():
        assert sanitize_for_founder(raw) == plain, raw
