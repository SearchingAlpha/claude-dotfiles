# Operating contract: Amazon work overlay

Overlay on the generic operating contract; where the two differ, this file is the more specific. Names in braces, such as `{report_email}`, are confidential values defined in the private values file (heading "Operating contract: private values"), imported after this one. If that file isn't loaded or doesn't define a name, the value is unset and the item that needs it doesn't apply: no default recipient (draft), no own schema and no sanctioned target (ask first), no pre-authorized live run (stop after the dry run). Say which value is missing and never guess one. Keep the values out of claude-dotfiles and any other GitHub repo; internal repos that already hold them are fine.

## Ask-first list: specifics
- Email (aws-outlook-mcp): `email_send` only to `{report_email}` (the standing default for report emails) or a recipient the user named in this conversation; anything else is `email_draft`. There is no default distribution list.
- Datanet: run with `DRY_RUN=true` first as a technical check, never as the end state. Live creates only inside a workstream the user explicitly authorized, currently the SCA Reservoir serving layer, whose jobs are `{serving_layer_jobs}`; there, go from DRY_RUN to live without asking. Anywhere else, stop after the dry run and show the plan.
- Redshift and Reservoir writes: only to the user's own schemas, `{own_schema}`. Read anything you are granted.
- Sanctioned shared targets, routine for their workflows (no extra go-ahead):
  - SharePoint: WBR and adherence reports publish to the EU S&OP team site's WBR library, `{wbr_library}`, flat, week in the filename (`inbound-bridge_<YYYY-WW>.html`).
  - SCA team account `{team_account}`: deploys only through the package's own deploy scripts (`deploy/`, e.g. `deploy/release.sh`), never ad hoc CLI or console changes to its resources. A prod release is the user's call unless the task says so.
  - Every other shared target stays ask-first.
- No secrets in the Obsidian vault: it is the Pablomv-vault package, scanned by Amazon's Shepherd secret scanner (unrelated to the shepherd harness); a key there already raised a Sev-2.5.

## Landing work: specifics
- Obsidian vault (/local/home/pablomv/obsidian-vaults/vault, Pablomv-vault, branch `mainline`): the standing vault auto-commit rule wins. Commit once per turn on `mainline` (that turn's changes in one commit, specific paths, never `-A`) and push, without asking; on a rejected push, `git pull --rebase` and push again. Never force-push. If the vault_commit guard is enabled for the vault (`vault_commit.path` in ~/.claude/guards/config.json, or CLAUDE_GUARDS_VAULT), leave committing and pushing to it.
- Other git.amazon.com repos: the user handles pushing, so never `git push` there. Their main branch is `mainline`, which the built-in background-session rule (no pushes to main or master) does not cover.
- Enrolled git.amazon.com repos (SupplyChainAgents, SupplyChainAgents-Slack): `shepherd pr`, which picks the CRUX backend from the remote and opens a draft CR (shepherd-crux skill).
- Enrolled GitHub repos (claude-dotfiles, remote github.com/SearchingAlpha): `shepherd pr` opens a GitHub PR.
- git.amazon.com repos not enrolled (e.g. IPT-delta-generator): `cr --new-review --no-open` from the branch, or stop and report the branch and path.
- If `shepherd pr` or `cr` fails (known: "Your package is unmodified" for track worktrees under ~/workplace): commit on the branch, don't push, report the branch, worktree and error.

## Definition of done: local specifics
- UI screenshots: the Playwright MCP exists only in sessions started in /local/home/pablomv, uigen and scada-sim. Elsewhere run `/home/pablomv/.local/chrome/opt/google/chrome/chrome --headless --no-sandbox --screenshot=<path> <url>`.
- redshift-mcp rejects SQL that starts with a comment (`--` or `/* */`) or a `(`: start with the keyword and put comments after it.

## Compact Instructions (additions)
When summarizing, also keep: CR ids and URLs, the WBR week and its probe state (what was probed when, first cut or final), Datanet ETL and job ids with whether each ran DRY_RUN or live, and Redshift tables written.
