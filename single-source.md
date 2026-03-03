# Project: One source, multiple tools

I work with Claude Code in three different environments:

- ~/.config/claude/personal
- ~/.config/claude/positron
- ~/.config/claude/git-ai

And I also work with some other agentic workflow tools: Factory’s [Droid](https://docs.factory.ai/welcome) and [OpenCode](https://opencode.ai/docs/).

All of these three tools allow the user to define custom prompts, agents, MCP servers, and sometimes skills.

Use python-pro to create a scheme for this project that allows me to do the following:

1. Define one set of commands, agents and skills that can be “deployed” to all these environmets, unless that command, agent or skill has been specially marked somehow to only deploy to certain environment, or to deploy everywhere except certain environments.

2. A “server per Yaml file” way to define user-scope and project-scope MCP servers, and a way to deploy these to both my user level and project level tool configurations in the manner that each such tool expects. It should also be possible in the Yaml to “disable” a particular MCP server, or filter which environments it should be used with, as above.

3. When I deploy to my various projects and user environment, and there are other commands already present (such as those intalled by TaskMaster), then those pre-existing items are left alone.
