I want to update some module.

But because this module is documentation driven, it is not that easy. So based on my request, follow the pipeline to apply changes:

1. update corresponding .md files first
2. then for the modules with updated .md files (or modules affected by it) - update contract tests so test expect the updated behavriour (tests for now red)
3. spawn an agent to implement changes in code
4. validate their work, make sure test became green
