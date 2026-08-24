# copier-template

Copier template for scaffolding Python, PyO3, Rust, Go, and shell projects with a shared
pre-commit, CI/CD, and packaging setup.

## Prerequisites

- [Copier](https://copier.readthedocs.io)

## Installation

No installation is needed. Copier fetches this repo directly.

## Usage

```shell
copier copy gh:audivir/copier-template <destination>
```

To pull in template updates later:

```shell
copier update
```

Project type, versions, and other options are selected via prompts on first run.
See `copier.yml` for the full list of questions.

### Self-bootstrapping

This repo applies the template to itself (`project_type: bootstrap`), skipping the
`update-copier-template` workflow since a template cannot depend on its own commit to
update itself. `.copier-answers.yml` uses `_src_path: .` with no `_commit`. Refresh it
after editing `template/` with `copier recopy` (not `copier update`, which needs `_commit`):

```shell
copier recopy --skip-answered --defaults --trust --overwrite
```

## License

MIT, see `LICENSE`.
