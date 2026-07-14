#!/usr/bin/env bash
set -euo pipefail

if ! ROOT_DIR=$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd); then
  echo "Unable to resolve the Goal Plus checkout" >&2
  exit 1
fi

readonly CANNBENCH_REPOSITORY_URL="https://gitcode.com/cann/cann-bench.git"
readonly CANNBENCH_REVISION="da92996f420c59727c1769aecd30c7cd07549b31"
readonly CANNBENCH_BRANCH="master"
readonly AKG_REPOSITORY_URL="https://gitcode.com/mindspore/akg.git"
readonly AKG_REVISION="a2c1a23fd371e234b7e767247e8c4753462ecdca"
readonly AKG_BRANCH="br_agents"
readonly CANNBOT_REPOSITORY_URL="https://gitcode.com/cann/cannbot-skills.git"
readonly CANNBOT_REVISION="d5ddcacc6e51eeaa8b52fa446c3b768c6813602e"
readonly CANNBOT_BRANCH="master"

if [[ -n ${XDG_CACHE_HOME:-} ]]; then
  default_dependency_cache="$XDG_CACHE_HOME/goal-plus/ascendc-e2e"
elif [[ -n ${HOME:-} ]]; then
  default_dependency_cache="$HOME/.cache/goal-plus/ascendc-e2e"
else
  default_dependency_cache="${TMPDIR:-/tmp}/goal-plus-ascendc-e2e-cache"
fi
dependency_cache=${GOAL_PLUS_E2E_CACHE_DIR:-$default_dependency_cache}
if ! mkdir -p "$dependency_cache" 2>/dev/null; then
  echo "Unable to create the AscendC E2E dependency cache" >&2
  exit 1
fi
if ! dependency_cache=$(cd -P "$dependency_cache" 2>/dev/null && pwd); then
  echo "Unable to resolve the AscendC E2E dependency cache" >&2
  exit 1
fi

repository_root() {
  local path=$1
  git -C "$path" rev-parse --show-toplevel 2>/dev/null
}

require_revision() {
  local name=$1
  local repository=$2
  local revision=$3
  if ! git -C "$repository" cat-file -e "${revision}^{commit}" 2>/dev/null; then
    echo "$name checkout does not contain the required revision $revision" >&2
    return 1
  fi
}

cached_checkout() {
  local name=$1
  local url=$2
  local revision=$3
  local branch=$4
  local target="$dependency_cache/${name}-${revision}"

  if [[ ! -d "$target/.git" ]]; then
    local temporary="${target}.tmp.$$"
    rm -rf "$temporary" 2>/dev/null
    if ! git clone --quiet --filter=blob:none --no-checkout \
      --single-branch --branch "$branch" "$url" "$temporary" \
      >/dev/null 2>&1; then
      rm -rf "$temporary" 2>/dev/null
      echo "Unable to clone the pinned $name dependency from $url" >&2
      return 1
    fi
    if ! git -C "$temporary" checkout --quiet --detach "$revision" \
      >/dev/null 2>&1; then
      rm -rf "$temporary" 2>/dev/null
      echo "The pinned $name revision is unavailable from $url" >&2
      return 1
    fi
    if ! mv -T "$temporary" "$target" 2>/dev/null; then
      # Another launcher may have atomically published the same pinned checkout.
      rm -rf "$temporary" 2>/dev/null
      if [[ ! -d "$target/.git" ]]; then
        echo "Unable to publish the pinned $name dependency cache" >&2
        return 1
      fi
    fi
  fi

  require_revision "$name" "$target" "$revision"
  if [[ $(git -C "$target" rev-parse HEAD 2>/dev/null) != "$revision" ]]; then
    echo "$name cache is not at its pinned revision; remove that cache entry and retry" >&2
    return 1
  fi
  printf '%s\n' "$target"
}

resolve_dependency() {
  local name=$1
  local override=$2
  local url=$3
  local revision=$4
  local branch=$5

  if [[ -n "$override" ]]; then
    local root
    if ! root=$(repository_root "$override"); then
      echo "$name override is not inside a Git checkout" >&2
      return 1
    fi
    require_revision "$name" "$root" "$revision"
    printf '%s\n' "$root"
    return
  fi
  cached_checkout "$name" "$url" "$revision" "$branch"
}

detect_conda_sh() {
  if [[ -n ${GOAL_PLUS_NPU_CONDA_SH:-} ]]; then
    printf '%s\n' "$GOAL_PLUS_NPU_CONDA_SH"
    return
  fi
  if [[ -n ${CONDA_EXE:-} ]]; then
    local candidate
    candidate="$(dirname "$(dirname "$CONDA_EXE")")/etc/profile.d/conda.sh"
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  fi
  if command -v conda >/dev/null 2>&1; then
    local candidate
    candidate="$(dirname "$(dirname "$(command -v conda)")")/etc/profile.d/conda.sh"
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  fi
}

detect_npu_env_sh() {
  if [[ -n ${GOAL_PLUS_NPU_ENV_SH:-} ]]; then
    printf '%s\n' "$GOAL_PLUS_NPU_ENV_SH"
    return
  fi
  if [[ -f "$ROOT_DIR/../env.sh" ]]; then
    printf '%s\n' "$ROOT_DIR/../env.sh"
    return
  fi
  if [[ -n ${ASCEND_HOME_PATH:-} && -f ${ASCEND_HOME_PATH}/set_env.sh ]]; then
    printf '%s\n' "${ASCEND_HOME_PATH}/set_env.sh"
  fi
}

source_private_environment() {
  local description=$1
  local path=$2
  # Environment changes must remain in this shell, while private setup output is hidden.
  # shellcheck disable=SC1090
  if ! source "$path" >/dev/null 2>&1; then
    echo "Unable to initialize $description" >&2
    return 1
  fi
  set -e
}

GOAL_PLUS_NPU_CONDA_SH=$(detect_conda_sh)
if [[ -n "$GOAL_PLUS_NPU_CONDA_SH" ]]; then
  if [[ ! -f "$GOAL_PLUS_NPU_CONDA_SH" ]]; then
    echo "GOAL_PLUS_NPU_CONDA_SH does not name a readable file" >&2
    exit 1
  fi
  source_private_environment "Conda for the AscendC E2E test" \
    "$GOAL_PLUS_NPU_CONDA_SH"
fi

GOAL_PLUS_NPU_ENV_SH=$(detect_npu_env_sh)
if [[ -n "$GOAL_PLUS_NPU_ENV_SH" ]]; then
  if [[ ! -f "$GOAL_PLUS_NPU_ENV_SH" ]]; then
    echo "GOAL_PLUS_NPU_ENV_SH does not name a readable file" >&2
    exit 1
  fi
  source_private_environment "the NPU environment for the AscendC E2E test" \
    "$GOAL_PLUS_NPU_ENV_SH"
elif [[ ${GOAL_PLUS_SKIP_NPU_ENV_SOURCE:-0} != 1 ]]; then
  echo "NPU environment setup was not found; set GOAL_PLUS_NPU_ENV_SH" >&2
  exit 1
fi
export GOAL_PLUS_NPU_CONDA_SH GOAL_PLUS_NPU_ENV_SH

cannbench_override=${CANNBENCH_ROOT:-}
akg_skills_override=${AKG_ASCENDC_SKILLS_ROOT:-}
cannbot_override=${CANNBOT_SKILLS_ROOT:-}

CANNBENCH_ROOT=$(resolve_dependency \
  "CANNBench" "$cannbench_override" "$CANNBENCH_REPOSITORY_URL" \
  "$CANNBENCH_REVISION" "$CANNBENCH_BRANCH")
akg_repository_root=$(resolve_dependency \
  "AKG" "$akg_skills_override" "$AKG_REPOSITORY_URL" \
  "$AKG_REVISION" "$AKG_BRANCH")
CANNBOT_SKILLS_ROOT=$(resolve_dependency \
  "CANNBot skills" "$cannbot_override" "$CANNBOT_REPOSITORY_URL" \
  "$CANNBOT_REVISION" "$CANNBOT_BRANCH")

if [[ -n "$akg_skills_override" ]]; then
  AKG_ASCENDC_SKILLS_ROOT=$akg_skills_override
else
  AKG_ASCENDC_SKILLS_ROOT="$akg_repository_root/akg_agents/python/akg_agents/op/resources/skills/ascendc"
fi
if [[ ! -d "$AKG_ASCENDC_SKILLS_ROOT" ]]; then
  echo "AKG AscendC skills directory is missing at the pinned revision" >&2
  exit 1
fi

export CANNBENCH_ROOT AKG_ASCENDC_SKILLS_ROOT CANNBOT_SKILLS_ROOT
export GOAL_PLUS_RUN_ASCENDC_NPU_ST=1
export ST_ASCENDC_NPU_TIMEOUT=${ST_ASCENDC_NPU_TIMEOUT:-10800}

private_paths=(
  "${HOME:-}"
  "$(dirname "$ROOT_DIR")"
  "$ROOT_DIR"
  "$dependency_cache"
  "$cannbench_override"
  "$akg_skills_override"
  "$cannbot_override"
  "$CANNBENCH_ROOT"
  "$AKG_ASCENDC_SKILLS_ROOT"
  "$CANNBOT_SKILLS_ROOT"
  "$GOAL_PLUS_NPU_CONDA_SH"
  "$GOAL_PLUS_NPU_ENV_SH"
  "${CONDA_PREFIX:-}"
  "${ASCEND_HOME_PATH:-}"
)

redact_stream() {
  local line path
  while IFS= read -r line || [[ -n "$line" ]]; do
    for path in "${private_paths[@]}"; do
      if [[ -n "$path" && "$path" != / ]]; then
        line=${line//"$path"/'$LOCAL_PATH'}
      fi
    done
    printf '%s\n' "$line"
  done
}

run_redacted() {
  "$@" > >(redact_stream) 2> >(redact_stream >&2)
}

if ! cd "$ROOT_DIR" 2>/dev/null; then
  echo "Unable to enter the Goal Plus checkout" >&2
  exit 1
fi

if [[ ${GOAL_PLUS_E2E_INSTALL:-1} == 1 ]]; then
  run_redacted python -m pip install -e ".[dev]"
fi

command -v goal-plus >/dev/null
command -v pi >/dev/null
command -v goal-plus-pi-tool >/dev/null
command -v goal-plus-pi-worker >/dev/null

run_redacted python -m pytest \
  tests/st_pi/test_ascendc_npu.py \
  -m "st_npu and st_pi" \
  -q \
  -s
