#!/bin/zsh
# Plan 14.3, round 3: each idea of PROPOSTA-14.3.md against P0, the proposed text of each prompt
# against the code's, and two controls (the code's prompt against itself: the noise floor).
# The judge reads every pair in both orders (7b47664). Logs in ~/.loompa/bench/logs/r3;
# `uv run python docs/bench/summarize.py ~/.loompa/bench/logs/r3` makes the table.
#   docs/bench/run_round3.sh [control-dir]   # control-dir: the code's prompts exported as <key>.txt
cd "/Users/gustavo/Projects/Loompa LTDA"
M=openrouter:xiaomi/mimo-v2.6-flash
C=docs/bench/candidates
CTRL=${1:-}
LOGS=~/.loompa/bench/logs/r3
# The Inspector reads whole specs since 9667287 (docs/bench/fullspec_cases.py); its calls take ~5 min
# each on the bench model, so 30 cases.
FULLSPEC=(--from fullspec-contas --from fullspec-tamagotchi-retro --limit 30)
mkdir -p $LOGS
run() { # name prompt [args...]
  local name=$1 prompt=$2; shift 2
  env -u FORCE_COLOR uv run loompa prompts compare $prompt --model-a $M --model-b $M --limit 60 \
    --factory tamagotchi-retro "$@" > $LOGS/$name.log 2>&1
}
vs_p0() { # name prompt idea [args...]
  local name=$1 prompt=$2 idea=$3; shift 3
  run $name $prompt --candidate-a $C/$prompt.p0-agnostic.txt -c $C/$prompt.$idea.txt "$@"
}

# The Inspector's slow track runs beside the rest (disowned, so the waves' `wait` skips it): P0,
# scope (P3) and the proposed text
rm -f $LOGS/.inspector-done
(
  run p0-inspector inspector.JUDGE_SYSTEM -c $C/inspector.JUDGE_SYSTEM.p0-agnostic.txt $FULLSPEC &
  vs_p0 p3-inspector inspector.JUDGE_SYSTEM p3-scope $FULLSPEC &
  run proposed-inspector.JUDGE_SYSTEM inspector.JUDGE_SYSTEM -c $C/inspector.JUDGE_SYSTEM.proposed.txt $FULLSPEC &
  wait
  touch $LOGS/.inspector-done
) &!

# 0. Noise floor: the code's prompt against itself
if [[ -n $CTRL ]]; then
  run control-exec master.EXEC_SYSTEM -c $CTRL/master.EXEC_SYSTEM.txt &
  run control-summary deployer.SUMMARY_SYSTEM -c $CTRL/deployer.SUMMARY_SYSTEM.txt &
fi
# 1. Founder text: P0, the voice (P1) and the `impact` conflict (P6)
run p0-exec master.EXEC_SYSTEM -c $C/master.EXEC_SYSTEM.p0-agnostic.txt &
vs_p0 p1-exec master.EXEC_SYSTEM p1-founder-voice &
vs_p0 p6-exec master.EXEC_SYSTEM p6-conflicts &
vs_p0 p1-summary deployer.SUMMARY_SYSTEM p1-founder-voice &
wait
run p1-report master.SPRINT_REPORT_SYSTEM -c $C/master.SPRINT_REPORT_SYSTEM.p1-founder-voice.txt &
run p0-summary deployer.SUMMARY_SYSTEM -c $C/deployer.SUMMARY_SYSTEM.p0-agnostic.txt &
# 2. The spec: P0, examples (P4) and when to ask (P10)
run p0-spec product.SYSTEM -c $C/product.SYSTEM.p0-agnostic.txt --mode final &
vs_p0 p4-spec product.SYSTEM p4-examples --mode final &
vs_p0 p10-spec product.SYSTEM p10-when-to-ask --mode final &
run p4b-review product_owner.REVIEW_SYSTEM -c $C/product_owner.REVIEW_SYSTEM.p4b-testable-note.txt &
wait
# 3. Planning and review: P0 and conflicts (P6), recommended option first (P9), scope (P3)
run p0-architect architect.SYSTEM -c $C/architect.SYSTEM.p0-agnostic.txt &
vs_p0 p6-architect architect.SYSTEM p6-conflicts &
vs_p0 p6-preflight architect.PREFLIGHT_SYSTEM p6-conflicts &
run p6-propose product_owner.PROPOSE_SYSTEM -c $C/product_owner.PROPOSE_SYSTEM.p6-conflicts.txt &
vs_p0 p6-review product_owner.REVIEW_SYSTEM p6-conflicts &
run p9-analyst analyst.SYSTEM -c $C/analyst.SYSTEM.p9-recommended-first.txt &
wait
vs_p0 p9-preflight architect.PREFLIGHT_SYSTEM p9-recommended-first &
run p0-align product_owner.ALIGN_SYSTEM -c $C/product_owner.ALIGN_SYSTEM.p0-agnostic.txt &
run p0-amend architect.AMEND_SYSTEM -c $C/architect.AMEND_SYSTEM.p0-agnostic.txt &
run p0-review product_owner.REVIEW_SYSTEM -c $C/product_owner.REVIEW_SYSTEM.p0-agnostic.txt &
wait
# 4. First calls only (no judge: a judge cannot weigh a tool call): a safety check that the
#    Worker's and the brainstorm's opening does not change; their real effect is live
run p5-worker worker.SYSTEM -c $C/worker.SYSTEM.p5-general-solution.txt &
run p7-worker worker.SYSTEM -c $C/worker.SYSTEM.p7-test-seams.txt &
vs_p0 p2-architect architect.SYSTEM p2-tracer &
run p8-brainstorm brainstorm.BRAINSTORM_SYSTEM -c $C/brainstorm.BRAINSTORM_SYSTEM.p8-ask-round.txt &
wait
# 5. The proposed text of each prompt against the code's
n=0
for f in $C/*.proposed.txt; do
  prompt=${${f:t}%.proposed.txt}
  [[ $prompt == inspector.JUDGE_SYSTEM ]] && continue  # in the slow track above
  run proposed-$prompt $prompt -c $f &
  (( ++n % 6 == 0 )) && wait  # six at a time, as in round 2
done
wait
while [[ ! -f $LOGS/.inspector-done ]]; do sleep 15; done
echo "round 3 done"
