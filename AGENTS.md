# Repo structure

This repo has rather specific repo structure that you need to follow.

## Modules-Services and Models

Repo follows a unique notation (different from accepted in programming), so be careful.

All public classes with a business logic are called "Services".
Also, there are "Models" describing data models of how these "Services" interact with each other. Models are stored in separate "models" module. Services use them to communicate with each other.

Each model must live in its own Python file under `assistant_api.models`. Models do not have Markdown documentation files.

Each module (dir with `__init__.py` file) has a single (rarely more) services publicly declared in its `__init__.py` file. All other files are considered private to this module and can not be used by other modules.

Each "Service" has its own documentation md file describing its public contracts (so other services can use it). It means that during development, you CAN NOT inroduce new modules and sub-modules - keep the files of the modules flat. Each new module (dir with `__init__.py` file) is a bid deal and need to be documented and approved by user.

So that is it. Whole repo is just a set of public "Services" and models. Everything else should be hidden inside the module.

Some complex "Services" may consist of several "Sub-services" (sub-modules) - they have its own documentation and public contract.

Public service docs use Obsidian-style links as a tree:
- Entrypoint docs may link to interface services they compose.
- Interface docs must not link to their users or implementations.
- Implementation docs may link to the interface they implement or use.
- Implementation docs must not link to sibling implementations or users.

## Colocated tests

We use pytest for testing.

Tests are colocated in each module they test.

Ну вот, а-а-а, у нас есть, получается, у каждого сервиса контракт. Так вот, а-а-а, нужно, чтоб тесты покрывали, ну, что, а-а-а, сервис удовлетворяет этому контракту. То есть буквально под каждое требование у нас должен быть какой-то тест, который тестирует, что это действительно так. Ну, ну, в идеале. Вот, соответственно, а-а-а, также могут быть unit-тесты, которые уже тестируют, а-а-а, непосредственно реализацию, но нужно, чтоб они были разделены от вот этих вот, ну, тестов контракта, да? То есть, э-э-э, и вот эти как раз, ну, тесты контракта, они будут тестировать только вот этот как раз публичный сервис на удовлетворение требованиям из документации

So the structure of the service's tests should be

service_dir
- tests
  - unit
  - contract

## Container and external binary QA

Any code path that calls an external binary inside the generated assistant container must have a `live_container` test covering the real binary, command name, and required flags in the generated container.

Fakes must model real tool behavior conservatively. Unknown commands must fail in fakes, and fake-only commands are forbidden unless explicitly documented as test-only.

Manual tests are only for human OAuth or third-party state that cannot be automated. Docker, FUSE, rclone, and generated container runtime behavior must be covered by non-manual tests and must run before release.

When a release changes generated container runtime behavior, validation must include a fresh install of the published PyPI version plus at least the affected `live_container` smoke test.

## Docs-first

Each module-service has a documentation file(-s) in the module root. This doc file is approved by user and can not be silently changed. Doc files may be changed only if user explicitly approved/requested it. If not sure, ask user.

BUT! If during planning or implementation you asked user some clarifying questions - document user's decisions in the service docs.
Be concise, document only things EXPLICITLY told by user, not your assumptions. The main idea that no user's explicit decision should be lost - everything should be documented. Когда ты добавляешь что-то в документацию, сделай так, чтобы это выглядело как документация. То есть не нужно явно там говорить: пользователь только что попросил меня добавить то-то, то-то. Да, то есть это докумен-- файл документации, он должен выглядеть как документация. Не нужно в него включать какие-то комментарии, откуда ты взял эту информацию. То есть это каждый файл документации — это источник, а, истины. А, неважно, откуда, э, оттуда доба-- откуда туда добавляется различная информация. Ну, просто каждый кусочек требований — это требование. Не нужно, а, указывать, что пользователь явно это сообщил, откуда ты его взял, это требование. То есть, а, когда пишешь документацию, а, пиши документацию, а не логи, а, в этом файле

On the other hand, the documentation is only a public contract, not controlling the implementation. So you have a freedom of implementation. As long as it does not contradict with the modules' contract - write/modify code freely, without getting approval.

If you notice that several docs are contradicting, report to user, ask which one is the source of truth and remove from docs the contradicting part based on user's answer.

## Modifying single module at a time

The default working mode (unless user explicitly asked otherwise) is working with some single module specified by user. Avoid complex multi-module modifications.

In fact, try to avoid over-exploring the repo by reading unrelated to the current module files, focus only on what you are working right now and its dependencies. No need to explore "just for better understanding". 

# Env management

The venv and dependencies are managed through uv. Run everything through `uv run`

All env vars are managed through doppler project "notes_assistant". Check skill "working-with-env-vars-and-secrets" if you need to add new vars.

To run the app, use command like

```bash
doppler run -p notes_assistant -c dev -- uv run <your-command>
```

# Core development principles:
- Fail fast on unexpected situations
  - Zero fallbacks tolerance - fail fast, no defaults, no fallbacks.
  - If you really need a fallback - it should be documented in the docs. Ask user's approval before adding it to docs.
  - Silent degradation is forbidden unless documented. Fail fast.
- Zero legacy tolerance - make full proper refactorings
- No errors hiding
  - If something does not work because of the problem in users library or tg_auto_test or demo_ui - do not work around it. Stop and report to user.
  - Properly fix linter warnings, do not hide them. If needed - do proper refactoring. Do not be lazy. Choose proper solutions over easy.
- Keep code highly modularized:
  - Recommended file size is below 200 lines. More than 400 lines is forbidden and should be decomposed.
  - Each significant class should be a separate files
  - Tests should be logically grouped into separate files
- If the user explicitly asks you to commit, push, or publish a release, treat that as authorization to do so for this repo.

# Concurrent modifications
If you notice that some files are unexpectedly modified (not by you) - do not rever these changes,
probably the user is working on the same file. If it is blocking you - stop and contact the user to sync. Or if these changes are not breaking for you - proceed. But never revert unexpected changes!
