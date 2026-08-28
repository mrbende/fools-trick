#!/usr/bin/env bash
# Populate the environment for ops scripts from the ONE config loader. This is not a config
# file -- it holds no values and no defaults. It evals core/config.py, which reads config.yaml
# (the method) and deploy.yaml (the rig) and emits validated exports. One source, one parser.
_ft_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if _ft_env="$(cd "$_ft_root" && python3 -m core.config --env 2>/dev/null)"; then
  eval "$_ft_env"
else
  echo "env.sh: config could not be resolved (run: python3 -m core.config --check)" >&2
  return 1 2>/dev/null || exit 1
fi
unset _ft_env _ft_root
