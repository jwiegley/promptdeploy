Continue autonomously until all tasks are completed and parity is achieved.
Note that as you work, I want you to maintain and update a tasks and handoff
document so that we always know exactly what has been done, what remains, and
where and how we can pick up the task and execute to completion if anything
should happen to the machine and we need to start a fresh AI session.

Each time you make a commit now, I'd like you spawn a subagent that uses the
`fess` or `command-fess` skill to double-check the work you've added and the
claims it makes, factor in any fixes after that subagent runs into your main
development work. But don't run fess on commits you make to fix problems found
by fess. That risks the danger of getting us into a feedback loop where we are
unable to progress. "Don't let the prefect become the enemy of the good."
