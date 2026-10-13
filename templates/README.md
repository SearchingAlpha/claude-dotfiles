# templates

Instruction files meant to be imported into a `CLAUDE.md`, not loaded by the plugin. A `CLAUDE.md` at a plugin root isn't loaded as context, so the plugin can't ship them; you import them yourself. Nothing here is applied by `install.sh`.

| File | What |
|---|---|
| `operating-contract.md` | The global operating contract: definition of done, evidence, an advisory ask-first list, landing work, a delegation brief and compaction instructions. 36 lines, about 900 tokens, generic. |
| `operating-contract.work.md` | Overlay for Amazon work: the report-email default, Datanet DRY_RUN and the authorized workstream, own schemas, the sanctioned shared targets (the WBR SharePoint library, SCA team-account deploys), the vault secret scanner, the vault auto-commit-and-push exception, CR versus GitHub PR landing and its fallback, the local Chrome path, extra compaction items. 28 lines, about 1,000 tokens; its confidential values are `{name}` placeholders. |
| `operating-contract.private.md.example` | Format of the private values file, with example values. The real one, `skills-amazon/contract/private.md`, is gitignored. |
| `tests/test_contract.py` | The [checks](#checks). |

## Import the operating contract

Import it at **user scope**, so it loads in every session on the machine. Add one line to `~/.claude/CLAUDE.md`, below what is already there:

```text
@~/claude-dotfiles/templates/operating-contract.md
```

How the import behaves (from the [memory docs](https://code.claude.com/docs/en/memory#import-additional-files)):

- `@path` takes a relative, absolute or `~/` path. A relative path resolves from the file holding the import, not the working directory. Imported files can import others, up to four hops deep.
- The import must sit outside backticks and code fences, which keep `@path` literal. Write a space in a path as `\ `; a quoted path isn't imported at all.
- Imports in user-scope files (`~/.claude/CLAUDE.md`, `~/.claude/rules/`) load without an approval dialog. The exception is Cowork desktop sessions, which skip user-scope imports that point outside the session's working directory.
- The file loads at launch with the `CLAUDE.md` that imports it, and costs the same context as pasting it in. Edits reach new sessions.
- Point at the main checkout, never a track worktree: `shepherd finish` deletes worktrees.

Specifics go in an overlay imported on the line after the contract, so they come later in context than the generic rules; see [Work overlay](#work-overlay-and-private-values).

To check it loaded: `/context` lists the instruction files loaded at launch, and `/memory` lists the memory files.

To check it for contradictions, use `/doctor prompt-audit` (v2.1.283+). By default it covers `CLAUDE.md`, `CLAUDE.local.md` and `AGENTS.md`, plus the rules, skills, commands, subagents and output styles under `.claude/` and `~/.claude/`. The docs don't say it follows an import outside those, or reads auto memory. So audit the contract by path, `/doctor prompt-audit ~/claude-dotfiles/templates/operating-contract.md`, and check your auto memory against it by hand.

The contract gives way to more specific instructions: a repo's own `CLAUDE.md`, `AGENTS.md` or rules, and your standing instructions for a specific repo or workflow, including those kept in memory (say, "commit and push my notes repo after every change"). The ask-first list is the exception: only you can waive it, in the conversation or in your own standing instructions (your `~/.claude/CLAUDE.md`, a private overlay, memory), never a committed repo file. Anyone who can commit to a repo can write "post a summary to the team channel" or "push the branch" into its `AGENTS.md`, so such a line still needs your go-ahead. The same goes for tool output and fetched pages.

**Project scope.** Don't put this import in a repo's committed `CLAUDE.md`. A path outside the project counts as an external import: Claude Code asks once per project to approve it, and teammates won't have the file. If a repo should carry the contract, copy the file into the repo and import it by a relative path, such as `@docs/operating-contract.md`.

## Work overlay and private values

`operating-contract.work.md` holds the rules for Amazon work. Everything in it is committed except the confidential values: the report recipient, schema and table names, the SharePoint site URL and the team AWS account id. The overlay names those `{report_email}`, `{own_schema}` and so on, and the private values file defines them; both load into context, and Claude fills the names in. If the private file isn't loaded, or lacks a name, the overlay's first paragraph turns each rule that needs the value into its conservative form: draft, ask first, stop after the dry run. Claude Code skips a missing import silently (a headless run in 2.1.295 printed nothing on stderr or in its debug log), so a lost file shows up only as drafts and questions where you expected sends.

1. **Values.** `skills-amazon/` is gitignored: merging the track doesn't carry the private file, and `shepherd finish` deletes it with the worktree. Copy it to the main checkout first:

   ```bash
   mkdir -p ~/claude-dotfiles/skills-amazon/contract
   cp ~/claude-dotfiles-cc-operating-kit/skills-amazon/contract/private.md ~/claude-dotfiles/skills-amazon/contract/
   ```

   On a new machine, copy `operating-contract.private.md.example` to `skills-amazon/contract/private.md` and fill it in. Keep each confidential literal in backticks: the checks read the values from there, and a code span keeps an address from being parsed as an `@` import.
2. **Imports.** At the end of `~/.claude/CLAUDE.md`, below the shepherd block, follow the contract line with the overlay, then the private values:

   ```text
   @~/claude-dotfiles/templates/operating-contract.md
   @~/claude-dotfiles/templates/operating-contract.work.md
   @~/claude-dotfiles/skills-amazon/contract/private.md
   ```

3. **Check.** In a new session, `/context` lists all three. Audit each one by path, `/doctor prompt-audit <path>`, then check the home-project MEMORY.md against them by hand. The known overlaps are settled: the vault push rule is an explicit exception in the overlay (one commit per turn, as the memory note's batching rule says, and none when the vault_commit guard is enabled for the vault), and the email default is in the private values.

**Enforcement gaps.** The overlay is advisory. `mcp__aws-outlook-mcp__email_send`, `mcp__redshift-mcp__execute_query` and `mcp__amazon-sharepoint-mcp__sharepoint_write_file` are in `permissions.allow` in `~/.claude/settings.local.json` (checked 2026-10-09), so nothing stops a send or a write today. Once applied, the Amazon guards (`recipient_guard` for email recipients, `datanet_guard` for the dry run first) and the settings gates (ask rules for the Outlook and SharePoint write tools) close the email, Datanet and SharePoint gaps; nothing checks the target schema of a Redshift write. Moving `email_send` from `allow` to `ask` by hand does it for email, but only after the cron report jobs draft instead of send: a `claude -p` run can't answer the prompt, so the send is denied.

**Optional.** Make the Playwright MCP available in every session, so the screenshot rule works outside the three projects that have it:
`claude mcp add --scope user playwright -- npx @playwright/mcp@latest --browser chrome --executable-path /home/pablomv/.local/chrome/opt/google/chrome/chrome --headless --no-sandbox`

**Follow-ups, not done here.** Once the overlay is imported, remove the "No secrets in the vault" entry from the home-project MEMORY.md (the overlay carries it), prune that file to one line per entry and fix its stale "plugin 1.3.0". Add the monthly line to the vault's `30-claude/tasks.md`: "Monthly: /doctor prompt-audit + /insights + memory prune".

## Checks

```bash
python3 -m unittest discover -s templates/tests
```

- The generic contract stays within 40 lines, has its six sections and no placeholders.
- Every `{name}` in the overlay has an entry in the example, and the other way round; each overlay section extends a generic one.
- No file under `templates/` has the shape of confidential data: an email address outside `example.com`, a 12-digit account id other than the AWS docs' `123456789012`, a SharePoint host, or an internal `*.a2z.com`, `*.aws.dev` or `*.amazon.com` host. `git.amazon.com` and `code.amazon.com` as plain domain words are fine.
- With the private values present: they define exactly the example's names, with values of their own, and none of their backticked literals appears in a file under `templates/`. Without them these two tests skip and say so; `CONTRACT_PRIVATE=<path>` points them at another copy.

The rule they enforce: Amazon-flavoured text that isn't confidential (MCP server and tool names, project and script names, personal paths, workflow rules) is committed. Confidential values (account ids, internal hosts and URLs, schema and table names, business data, colleagues, the work email address, ticket ids) live only in the private file.

## What each section is for

| Section | Purpose |
|---|---|
| Definition of done | "Done" means the repo's check ran, UI changes were looked at on a screenshot, and failures were fixed at the root, not silenced. |
| Evidence | Claims come with the command, exit code and output tail, or with the query and row counts, so you can check them without rerunning. |
| Ask-first list | The lines not to cross without your go-ahead: external sends (a draft, unless you named the recipient or set a standing default), live writes to shared systems, secrets, and other people's resources. Only you can give the go-ahead, never a repo file or tool output. It is advisory: hooks and permission rules enforce, and their denials stay final. |
| Landing work | Enrolled repos land through `shepherd pr`. Elsewhere the session commits only when you or the task ask, on the current branch, as Claude Code does by default, and never pushes, merges or opens a review unless you ask. The exception is a background session preserving its work: Claude Code tells it to commit and push the branch, and [your git instructions take precedence](https://code.claude.com/docs/en/agent-view#how-file-edits-are-isolated), so it commits and leaves the push to you. |
| Delegation brief | Every Agent or Workflow prompt gets the same five parts. A prose result comes back as the final message, capped at 1,500 tokens, so subagent output doesn't flood the main context. A workflow agent given a schema returns JSON matching it instead ([workflows docs](https://code.claude.com/docs/en/workflows.md)), so the schema sets its size and the cap doesn't apply. A file is asked for only when another step consumes it: Claude Code's built-in subagent note (checked in 2.1.295) forbids report and summary files, and the read-only Explore and Plan agents can't write at all. Quoting untrusted text keeps it from being followed as instructions. |
| Compact Instructions | Tells the compaction summary what it must keep. |

## Compaction

Claude Code's built-in compaction prompt (checked in 2.1.295) tells the summarizer to follow "additional summarization instructions provided in the included context", and gives `## Compact Instructions` as its example. The [costs docs](https://code.claude.com/docs/en/costs#manage-context-proactively) show the same idea as a `# Compact instructions` section in `CLAUDE.md`. Nothing parses the heading: the summarizer reads the section as plain instructions, so what matters is a clearly labelled section that starts "When summarizing…".

In repos enrolled in shepherd, its SessionStart hook also fires after a compaction (source `compact`). For the session that owns the track, it re-injects the last entry logged under the track's name, plus a newer status line such as `review opened: <url>` (quoted; messages to the track are only counted). It adds the track's last five first-parent commits that neither its recorded base nor the base branch has, so after a `git pull` or a rebase by hand the base branch's commits aren't listed as the track's. The summary stays within 16 lines and 9,500 characters, because Claude Code moves hook context over 10,000 characters to a file and shows only a preview. If the session compacted outside its worktree, the summary names the worktree to go back to. From another worktree it says to run `ExitWorktree(action="keep")` first if EnterWorktree brought the session there, because EnterWorktree moves between existing worktrees only under `.claude/worktrees/`.
