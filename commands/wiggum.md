Continue autonomously until all tasks are completed and parity is achieved
(provided you have a known target, otherwise just work until all objectives of
the current plan have been completed and verified).

As you work, maintain and update a tasks and handoff document so that we
always know exactly what has been done, what remains, and where and how we can
pick up the task and execute it to completion if anything happens to the
machine and we need to start a fresh AI session.

Each time you make a commit, spawn a subagent that uses the `fess` or
`command-fess` skill to double-check the work you've added and the claims it
makes. Before spawning that subagent, read the commit description and choose the
audit scope: anywhere from 1 to 10 recent commits, inclusive, based on how much
surrounding history appears relevant to the changes under review. Use one commit
for isolated work; expand the range when the description suggests follow-up
work, stacked changes, refactors, shared infrastructure, earlier groundwork, or
claims whose truth depends on previous commits. Include the selected commit SHAs
and why that range was chosen. When spawning the subagent, include a context
snapshot in the prompt so the fess run can audit the selected commit range
against the full context that led to it. The snapshot must include the original
user request, the current plan and handoff state, relevant design decisions and
tradeoffs, notable commands and verification results, the commit SHA or SHAs
being audited, the files changed, and the specific claims the main agent has
made about the work. Preserve exact wording for requirements and claims when
practical; if the context is too large, include a dense summary plus any exact
excerpts needed to avoid losing intent. Factor in any fixes after that subagent
runs into your main development work. However, don't run fess on commits you
make to fix problems found by fess. That risks creating a feedback loop that
prevents us from making progress. "Don't let the perfect become the enemy of
the good."

After each commit, also check for partner review observations in
`doc/observations/`. If regular, non-hidden Markdown files are present, pause the
main work and run the `partner-cleanup` command or `command-partner-cleanup`
skill. Let that workflow address the captured observations through a subagent
and make its cleanup commit before resuming the original task. New observations
created by the reviewing partner after that cleanup commit can be handled in the
next cycle.
