#!/bin/zsh
# Plan 14.3, round 4: confirmation. Each prompt's final text (only the ideas round 3 approved,
# `FINAL` in make_candidates.py) against the code's, in a fresh run: a split seen once at n≈30 can
# be chance (two runs of the same Inspector comparison gave 15×5 and 9×12).
#   docs/bench/run_round4.sh [prompt ...]   # default: every final text
cd "/Users/gustavo/Projects/Loompa LTDA"
M=openrouter:xiaomi/mimo-v2.6-flash
C=docs/bench/candidates
LOGS=~/.loompa/bench/logs/r4
mkdir -p $LOGS
run() { local prompt=$1; shift
  env -u FORCE_COLOR uv run loompa prompts compare $prompt --model-a $M --model-b $M --limit 60 \
    --factory tamagotchi-retro -c $C/$prompt.final.txt "$@" > $LOGS/final-$prompt.log 2>&1; }
prompts=(${@:-${${(f)"$(ls $C/*.final.txt)"}:t:r:r}})
for p in $prompts; do
  case $p in
    master.SPRINT_REPORT_SYSTEM) run $p --from relabel-contas --from relabel-tamagotchi-retro & ;;  # labels of d1efdfd
    inspector.JUDGE_SYSTEM) run $p --from fullspec-contas --from fullspec-tamagotchi-retro --limit 30 & ;;
    product.SYSTEM|architect.SYSTEM) run $p --mode final & ;;
    *) run $p & ;;
  esac
done
wait
