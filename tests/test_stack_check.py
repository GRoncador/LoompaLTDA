from loompa.stack_check import foreign_files, mismatch_note


def test_source_files_outside_the_stack_are_named_by_language():
    plan = ["src/persistence.py", "tests/test_persistence.py", "src/app.js", "README.md"]
    assert foreign_files(plan, ["javascript"]) == {
        "Python": ["src/persistence.py", "tests/test_persistence.py"]
    }


def test_one_family_scripts_and_an_unknown_stack_are_never_a_mismatch():
    assert foreign_files(["web/app.ts", "vite.config.js"], ["TypeScript"]) == {}
    assert foreign_files(["db/001.sql", "scripts/run.sh", "src/"], ["Python"]) == {}
    assert foreign_files(["src/app.go"], []) == {}


def test_the_note_names_the_stack_and_the_files_for_the_model():
    note = mismatch_note({"Python": ["src/p.py"]}, ["JavaScript"])
    assert "JavaScript" in note and "src/p.py" in note and "explicitly" in note
