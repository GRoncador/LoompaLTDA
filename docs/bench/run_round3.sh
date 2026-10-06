#!/bin/zsh
# Plan 14.3, round 3: each idea of PROPOSTA-14.3.md against P0, and the proposed text against the
# code's. Not run yet: the founder reviews the candidates first. Logs in ~/.loompa/bench/logs/r3;
# `uv run python docs/bench/summarize.py ~/.loompa/bench/logs/r3` makes the table.
cd "/Users/gustavo/Projects/Loompa LTDA"
M=openrouter:xiaomi/mimo-v2.6-flash
C=docs/bench/candidates
LOGS=~/.loompa/bench/logs/r3
mkdir -p $LOGS
run() { # name prompt [args...]
  local name=$1 prompt=$2; shift 2
  env -u FORCE_COLOR uv run loompa prompts compare $prompt --model-a $M --model-b $M --limit 40 \
    --factory tamagotchi-retro "$@" > $LOGS/$name.log 2>&1
}
vs_p0() { # name prompt idea [args...]
  local name=$1 prompt=$2 idea=$3; shift 3
  run $name $prompt --candidate-a $C/$prompt.p0-agnostic.txt -c $C/$prompt.$idea.txt "$@"
}

# 1. Founder text: the voice (P1) and the `impact` conflict (P6), each against P0
vs_p0 p1-exec master.EXEC_SYSTEM p1-founder-voice &
vs_p0 p6-exec master.EXEC_SYSTEM p6-conflicts &
vs_p0 p1-summary deployer.SUMMARY_SYSTEM p1-founder-voice &
run p1-report master.SPRINT_REPORT_SYSTEM -c $C/master.SPRINT_REPORT_SYSTEM.p1-founder-voice.txt &
wait
# 2. The spec: examples (P4) and when to ask (P10)
vs_p0 p4-spec product.SYSTEM p4-examples --mode final &
vs_p0 p10-spec product.SYSTEM p10-when-to-ask --mode final &
run p4b-review product_owner.REVIEW_SYSTEM -c $C/product_owner.REVIEW_SYSTEM.p4b-testable-note.txt &
wait
# 3. Conflicts in planning and review (P6), recommended option first (P9)
vs_p0 p6-architect architect.SYSTEM p6-conflicts &
vs_p0 p6-preflight architect.PREFLIGHT_SYSTEM p6-conflicts &
run p6-propose product_owner.PROPOSE_SYSTEM -c $C/product_owner.PROPOSE_SYSTEM.p6-conflicts.txt &
vs_p0 p6-review product_owner.REVIEW_SYSTEM p6-conflicts &
run p9-analyst analyst.SYSTEM -c $C/analyst.SYSTEM.p9-recommended-first.txt &
wait
# 4. The Worker's first call (P5, P7) and the plan (P2): the bench sees only the start of the loop
run p5-worker worker.SYSTEM -c $C/worker.SYSTEM.p5-general-solution.txt &
run p7-worker worker.SYSTEM -c $C/worker.SYSTEM.p7-test-seams.txt &
vs_p0 p2-architect architect.SYSTEM p2-tracer &
run p8-brainstorm brainstorm.BRAINSTORM_SYSTEM -c $C/brainstorm.BRAINSTORM_SYSTEM.p8-ask-round.txt &
wait
# 5. The proposed text of each prompt against the code's
n=0
for f in $C/*.proposed.txt; do
  prompt=${${f:t}%.proposed.txt}
  run proposed-$prompt $prompt -c $f &
  (( ++n % 6 == 0 )) && wait  # six at a time, as in round 2
done
wait
