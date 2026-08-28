from pathlib import Path

path = Path("orchestrator/control_loop.py")
text = path.read_text(encoding="utf-8")

old = "\n".join([
    '    return [',
    '        ("operations", [python, str(root / "orchestrator/operations_runner.py"), "--root", base, "--state-root", str(runtime)]),',
    '        ("stage_01a", [python, str(root / "orchestrator/orchestrator.py"), "--root", base, "--state-root", str(runtime)]),',
    '        ("auto_repair_pre", [python, str(root / "orchestrator/auto_repair_runner.py"), "--root", base, "--state-root", str(runtime), "--once"]),',
    '        ("codex_stage_01b", [python, str(root / "orchestrator/codex_stage01b_runner_v2.py"), "--root", base, "--state-root", str(runtime)]),',
    '        ("auto_repair_post", [python, str(root / "orchestrator/auto_repair_runner.py"), "--root", base, "--state-root", str(runtime), "--once"]),',
    '        ("codex", [python, str(root / "orchestrator/codex_runner.py"), "--root", base, "--state-root", str(runtime), "--once"]),',
    '    ]',
])

new = "\n".join([
    '    return [',
    '        ("auto_repair_pre", [python, str(root / "orchestrator/auto_repair_runner.py"), "--root", base, "--state-root", str(runtime), "--once"]),',
    '        ("operations", [python, str(root / "orchestrator/operations_runner.py"), "--root", base, "--state-root", str(runtime)]),',
    '        ("stage_01a", [python, str(root / "orchestrator/orchestrator.py"), "--root", base, "--state-root", str(runtime)]),',
    '        ("codex_stage_01b", [python, str(root / "orchestrator/codex_stage01b_runner_v2.py"), "--root", base, "--state-root", str(runtime)]),',
    '        ("auto_repair_post", [python, str(root / "orchestrator/auto_repair_runner.py"), "--root", base, "--state-root", str(runtime), "--once"]),',
    '        ("codex", [python, str(root / "orchestrator/codex_runner.py"), "--root", base, "--state-root", str(runtime), "--once"]),',
    '    ]',
])

if text.count(old) != 1:
    raise SystemExit(f"expected exactly one intermediate stage block, found {text.count(old)}")

path.write_text(text.replace(old, new, 1), encoding="utf-8")
