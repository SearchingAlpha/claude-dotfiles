---
name: recap
description: Recap of the loose ends the user left in earlier Claude Code sessions — which sessions they drove, what each one's last message handed back, and which reviews, tracks and repos still need them. Use when the user asks what they were working on, what they left open, or to catch up after a break ("since Friday", "yesterday").
argument-hint: "[friday | yesterday | 2d | 2026-10-09]"
---

A **recap** turns `herd recap --json` into the short list a person reads first thing in the morning: the topics still waiting on them, what each one needs next, and in what order. It saves the topics for the Recap page of `herd serve` and changes nothing else. Never resume, merge, push, finish or answer anything for the user; say what to do and let them do it.

## Steps

1. **Collect.** Run `herd recap --json $ARGUMENTS`. If `herd` isn't on PATH, run `python3 ~/claude-dotfiles/harnesses/herd/herd.py recap --json $ARGUMENTS`. A bad window exits 2 with a hint: fix the argument and rerun.
   Done when you hold the JSON: `since`, `label`, `sessions`, `loose_ends` and `folded`.

2. **Build the topics** by the rules in [`TOPICS.md`](TOPICS.md): group the sessions, decide each topic's state from the data, and write its title, summary, actions and sessions.
   Done when every session and every loose end belongs to exactly one topic, and every open topic has a next step.

3. **Re-check what you can.** For each GitHub review URL (`github.com/…/pull/N`), run `gh pr view <url> --json state,mergedAt` and update its topic. Report any other review's state as of its `checked_at`, and say how long ago that was if it's more than a day.
   Done when no topic relies on a GitHub state you didn't re-check.

4. **Write the recap**, plain and short:
   - One opening line: how many sessions since `label`, how many topics are open, and whether anything blocks other work.
   - **Waiting on you:** the open topics, numbered, in the order `TOPICS.md` gives. For each, write a bold title, where it stands, and the next steps. For a `blocked` session, give its tmux pane so `herd go <name>` reaches it. Name sessions by `name`, never by session id.
   - **Finished:** one line per done topic.
   - **Suggested order:** one or two sentences.
   - Fold `folded` into one closing line ("+N short sessions").

   Done when every open topic appears once, and no session id, token or credential appears in the text.

5. **Save the topics for the dashboard.** Pipe them to herd as the JSON in `TOPICS.md`, in one command with a quoted heredoc:

   ```bash
   herd recap --save-summary <<'EOF'
   {"since": …, "intro": "…", "suggested": "…", "topics": [ … ]}
   EOF
   ```

   If herd answers "not a recap summary", fix what it names and rerun. Done when herd answers `saved N topics`.
