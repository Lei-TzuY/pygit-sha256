# Phase 162 — Status configuration precedence

Phase 162 connects status presentation behavior that already existed in pygit
to Git-style repository configuration, while keeping command-line overrides and
porcelain rules explicit.

## Configuration now honored

### `status.showStash`

- default: `false`
- true values: `true`, `yes`, `on`, `1`
- false values: `false`, `no`, `off`, `0`
- `--show-stash` overrides the configured value
- `--no-show-stash` suppresses it again

As in Git, stash information is meaningful in long status and porcelain v2.
Short status and porcelain v1 do not gain synthetic stash records.

### `status.showUntrackedFiles`

Supported values:

- `no`
- `normal` (default)
- `all`

Git boolean aliases are also accepted:

- true -> `normal`
- false -> `no`

An explicit `-u` / `--untracked-files` mode always overrides configuration.
The existing Phase 154 grouping rules remain unchanged.

### `status.aheadBehind`

- default: `true`
- controls detailed ahead/behind presentation for non-porcelain formats
- `--ahead-behind` and `--no-ahead-behind` override the configured default
- porcelain v1/v2 ignore the config default, matching Git, while still honoring
  explicit ahead/behind CLI switches

Short output with detailed counts disabled uses the Git-style form:

```text
## main...origin/main [different]
```

Porcelain v2 with `--no-ahead-behind` emits unknown counts:

```text
# branch.ab +? -?
```

Long status now also reports the upstream relationship.  With detailed counts
disabled, divergent tips use:

```text
Your branch and 'origin/main' refer to different commits.
```

When detailed counts are enabled, pygit reports ahead, behind, diverged, or
up-to-date state with Git-style wording.

## Precedence model

Phase 162 deliberately distinguishes "option omitted" from an explicit CLI
value.  The order is:

1. explicit command-line option
2. relevant repository config
3. Git-compatible built-in default

For `status.aheadBehind`, step 2 is skipped for porcelain formats because Git
documents that config variable as a default only for non-porcelain status.

## Architecture

`pygit/status_config.py` owns config parsing and precedence.  The status
renderers consume resolved values only.  `Repository.status()` is unchanged;
Phase 162 is presentation/configuration behavior rather than a new repository
state API.

`pygit/status_porcelain_v2.py` now accepts an `ahead_behind` rendering flag so
explicit `--no-ahead-behind` can output `# branch.ab +? -?` without changing the
rest of the v2 record protocol.

## Compatibility checks

Behavior was cross-checked against current Git documentation and native Git for:

- `status.showStash=true` in long and porcelain v2 output
- no stash records in short/porcelain v1
- `status.showUntrackedFiles=no|normal|all` and boolean aliases
- CLI `-u` overriding configured untracked mode
- `status.aheadBehind=false` producing non-detailed non-porcelain output
- porcelain v1/v2 ignoring `status.aheadBehind=false`
- explicit `--no-ahead-behind` producing `[different]` and `+? -?`

## Tests

`tests/test_phase162.py` covers defaults, config parsing, CLI overrides,
porcelain exceptions, stash framing, untracked modes, short branch summaries,
long upstream summaries, porcelain-v2 unknown counts, and help output.
