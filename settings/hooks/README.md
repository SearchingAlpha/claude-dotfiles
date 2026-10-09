# settings/hooks

`allow_repo_push.py` is a PermissionRequest hook. It approves a plain push of **one** repo to **one** URL, so a standing "commit and push without asking" rule (a notes vault, say) keeps working once [`gates.template.json`](../gates.template.json) makes every `git push` ask. Anything else gets no answer, and Claude Code prompts as usual.

```json
{"hooks": {"PermissionRequest": [{"matcher": "Bash", "hooks": [{
  "type": "command", "if": "Bash(git *)", "command": "python3",
  "args": ["-I", "{{DOTFILES}}/settings/hooks/allow_repo_push.py",
           "--repo", "/abs/path/to/repo", "--url", "{{VAULT_PUSH_URL}}",
           "--remote", "origin", "--branch", "mainline"],
  "timeout": 5}]}]}}
```

`args` makes it an exec-form hook: no shell, each element is one argument. `"if": "Bash(git *)"` keeps it off every non-git prompt. [`../overlays/amazon-gates.json`](../overlays/amazon-gates.json) has the entry this host uses, with the push URL from the private values file.

## What it approves

A Bash command that pushes, and whose every part is one of these, run in the repo's top level (not a repo nested in it):

- `cd <absolute or ./ path>`;
- `git [-C <dir>] add|commit|pull|push` (writes) or `status|log|diff` (reads);
- `echo <words>`.

Each part takes only these arguments:

- `add`: paths of files in the repo. No options, no directory (so not `.`, `./`, the repo path or `notes/`), no `:/` magic, no globs.
- `commit`: `-m`/`--message` and `-q`, bundles such as `-qm` included, and no pathspecs. Claude Code's `-m "$(cat <<'EOF' … EOF)"` form counts as the message, unless a line in it starts with `EOF`: bash 5.2 ends the `$(…)` early on such a line, and whatever follows would run.
- `pull`: `--rebase`, `--ff-only`, `-q`, then `<remote> [<branch>]`.
- `push`: `-q`, then nothing, `<remote>`, or `<remote> <branch>`/`HEAD`/`HEAD:<branch>`. `HEAD` and a bare push need HEAD on `<branch>`; a bare push also needs `@{push}` to be `<remote>/<branch>` and `push.default` not `matching`. Every push prompts when one of these is set:
  - `remote.<remote>.push`, `.mirror` or `.receivepack` (the program the remote end runs);
  - `push.default=upstream` (or `tracking`) with `branch.<branch>.merge` other than `refs/heads/<branch>`. git would then send the branch elsewhere: `git push origin mainline` would land on `other`. Only the explicit `<branch>:<branch>` and `HEAD:<branch>` forms still pass;
  - `push.recurseSubmodules=on-demand|only`, or `submodule.recurse=true` without it, which push submodules to their own URLs.
- **The push URL is pinned** by `--url`: `git remote get-url --push --all <remote>` must print exactly that. The command applies `pushurl`, `insteadOf` and `pushInsteadOf`, so `git remote set-url`, a `pushurl`, a URL rewrite or a second URL all make every push prompt again. Those `git config` commands aren't gated themselves, which is why the hook has to check.
- `log`: `--oneline`, `-<n>`, `-n <n>`, `--format=`/`--pretty=` without `%G` (which runs gpg), `--stat` and a few other display flags, revisions, paths in the repo. `--output=<file>` and anything else prompts.
- `diff`: `--stat`, `--shortstat`, `--numstat`, `--name-only`, `--name-status`, `--cached`/`--staged`, revisions, paths in the repo.
- For `log` and `diff`, every word that isn't a flag, before or after `--`, must read as a path inside the repo. Revisions such as `HEAD..origin/mainline` or `'HEAD~1'` do; an absolute path elsewhere, `~/…` or `../…` out of the repo doesn't. "Inside" is git's own test, for `add` too: an absolute path counts when a leading part of it resolves to the repo, so a path through a symlinked home passes; a relative path gets only the text check. The reason: given two paths and one outside the work tree, `git diff` is an implicit `diff --no-index` and prints that file, so `git diff ~/.netrc /dev/null && git push` would have shown it.
- `status`: anything.

How the parts may be joined:

- With `&&` or `;`. A `cd` must be followed by `&&`, so a failed `cd` stops the rest.
- After a `;` only reads may follow, so a write never runs in a directory the hook didn't check.
- A git or echo part may end in `2>&1` and pipe into `head` or `tail`, alone or with `-<n>` or `-n <n>`. So `git push -q origin HEAD:mainline 2>&1 | tail -2; git log --oneline -1; git status -sb | head -1` is approved.

Options are read as git reads them: `-qam` contains `-a`, so it prompts, and so do abbreviations git would accept, such as `--amen` or `--al`. `-F`/`--file`, `-C`/`-c`, `--template` and `--pathspec-from-file` prompt too. The command goes through a small shell lexer, checked against bash 5.2 and zsh 5.9. These all prompt:

- `$` and backticks outside single quotes and the heredoc form, so `$V` variables too;
- `||`, `&`, other redirections (`> file`, `2>/dev/null`, `-F - <<EOF` heredocs) and newlines between commands;
- a pipe into anything but `head`/`tail`;
- globs, braces and comments;
- `cd` to a bare name or through `..`;
- `git rm` and `git mv`. After a move or delete, `git add <old> <new>` stages it.

## Limits

- Deny rules still win, so `git push --force` never reaches the hook, and `git -C repo push --force`, which only asks, gets no answer from it.
- Git config and hooks that run programs (`core.sshCommand`, a `pre-push` hook) are out of scope: an ungated `git fetch` would run them as well. `push.followTags` is allowed: it adds annotated tags to a push, but to the same pinned URL and without force.
- On an internal error it answers nothing, so you get the normal prompt. It writes the error to stderr and appends it to `~/.claude/gates/allow_repo_push.log` (`CLAUDE_GATES_HOME` moves that folder, for tests and trials).
- A call takes 40 to 55 ms (`tools/bench_hook.py`, 10-09): Python start-up plus up to five git calls. It only runs on permission prompts for git commands, not on every tool call.
- A push hook can't approve bundled work without approving the other work too. A call that also runs python, `sed` or another repo keeps prompting; in a headless run that is a silent deny.
- **Upgrading.** When an overlay changes the hook's arguments, merge with `--override-key hooks.PermissionRequest`. Without it the merge keeps the old entry next to the new one, and the old, looser one would still answer `allow`.

## Tests and tools

| Command | What |
|---|---|
| `python3 -m unittest discover -s settings/tests -p 'test_allow*'` | The hook as Claude Code runs it, on temp repos: payload on stdin, decision on stdout. Its lexer is compared word for word with bash and zsh (zsh skipped if missing). |
| `python3 settings/tools/fuzz_lexer.py [N] [SEED]` | Random commands from shell-significant pieces; every one the lexer accepts must split into the same words in bash and zsh. |
| `python3 settings/tools/mutate_hook.py` | Mutates a copy of the hook in a temp folder; every mutation must make a test fail. Takes several minutes. |
| `python3 settings/tools/bench_hook.py` | Median and worst latency per command shape. |
