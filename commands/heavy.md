Remember the following:

- Use the claude-code skill to provide context for the rest of the task
- Use beads to keep track of tasks you notice you work, and then complete those tasks in order to fully complete the initial task
- Use the GitHub CLI (`gh`) for all GitHub-related tasks
- Search the codebase for relevant files
- Ensure code passes linting and type checking after doing any work
- Use cpp-pro, python-pro, emacs-lisp-pro, rust-pro or haskell-pro as needed for diagnosing and analyzing PRs, fixing code, and writing any new code.
- If this worktree is anywhere under the "positron" or "pos" directories, then use pal to confer with gemini-3-pro-preview and gpt-5.2-pro to reach consensus on your deep analysis and review.
- Use Web Search and Perplexity with the web-searcher skill as needed for research and discovering resources.
- Use sequential-thinking when appropriate to break down tasks further.
- Use context7 whenever code examples might help.
- Use the Notion MCP server to query for documents and supporting information from Positron’s Notion document repository. Some of that information may be out of date or no longer accurate, but there are a lot of details there that might help you in your research.
- Use `andoria make` for building on a Linux machine to test any changes that you make.
- You can ssh to andoria-08 and within a `tron/work/<WORKTREE NAME>` directory use `nix develop --command FOO` to run any arbitrary command `FOO` on that machine.

Think deeply to analyze the following query, use pal to build consensus among
your partner LLMs and construct a well thought out plan of action based on the
following context, and then carefully execute that plan step by step:
