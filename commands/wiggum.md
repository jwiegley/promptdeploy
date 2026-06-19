Continue autonomously until all tasks are completed and parity is achieved
(provided you have a known target, otherwise just work until all objectives of
the current plan have been completed and verified).

As you work, maintain and update a tasks and handoff document so that we
always know exactly what has been done, what remains, and where and how we can
pick up the task and execute it to completion if anything happens to the
machine and we need to start a fresh AI session.

Each time you make a commit, spawn a subagent that uses the `fess` or
`command-fess` skill to double-check the work you've added and the claims it
makes. Factor in any fixes after that subagent runs into your main development
work. However, don't run fess on commits you make to fix problems found by
fess. That risks creating a feedback loop that prevents us from making
progress. "Don't let the perfect become the enemy of the good."
