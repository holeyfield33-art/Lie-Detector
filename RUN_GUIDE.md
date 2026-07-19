# Run Guide

Everything you need to get `liedetector` running locally: environment setup,
credentials, every command, and troubleshooting for the platform quirks we've
hit in practice (this guide was written and verified on Windows with Docker
Desktop + WSL2).

## 1. Prerequisites

| Requirement | Notes |
| --- | --- |
| Python 3.11+ | `python --version` |
| Git | used to clone target repos and to freeze the commit under test |
| Docker | a running daemon; the sandbox image is pinned by digest, never by tag |
| An LLM credential | Anthropic **or** an OpenAI-compatible provider (Featherless, OpenAI, OpenRouter, local llama.cpp) |

### Docker on Windows

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/)
with the WSL2 backend (default on modern installs). You do **not** need to run
`liedetector` itself inside WSL/Ubuntu — the Python CLI runs fine natively on
Windows and talks to the Docker daemon exposed by Docker Desktop. Verify it's
up:

```bash
docker version         # both a Client and a Server section should print
docker run --rm hello-world
```

If `docker` isn't found or the daemon is unreachable, start Docker Desktop and
wait for it to report "Engine running", then retry.

## 2. Install

```bash
git clone <this-repo>
cd Lie-Detector
pip install -e ".[dev,openai]"   # drop ",openai" if you only ever use Anthropic
```

`.[dev]` brings in `pytest`, `mypy`, `ruff`; `.[openai]` brings in the `openai`
SDK used by the OpenAI-compatible provider (Featherless, etc.). Anthropic
support is always installed (it's a core dependency).

## 3. Credentials

Copy the example file and fill in whichever provider you're using:

```bash
cp .env.example .env
```

`.env` is **git-ignored** and loaded automatically by the CLI from the current
directory (it never overrides a variable already set in your shell). Never put
a real key in `.env.example` — it's tracked and must stay placeholder-only.

### Anthropic (default provider)

```bash
# in .env
ANTHROPIC_API_KEY=sk-ant-...
```

Or use a stored `ant auth login` profile — the SDK resolves it automatically
if `ANTHROPIC_API_KEY` isn't set.

### OpenAI-compatible (Featherless, OpenAI, OpenRouter, ...)

```bash
# in .env
OPENAI_API_KEY=your-key-here          # or FEATHERLESS_API_KEY
OPENAI_BASE_URL=https://api.featherless.ai/v1
```

Select this provider per-command with `--provider openai`. `--base-url` on
the command line overrides `OPENAI_BASE_URL` if you pass both.

### Verify your setup

```bash
liedetector doctor
```

This checks: git, the Docker daemon, both provider SDKs, and which
credentials are present. It exits non-zero only if git or Docker is missing —
missing credentials are a warning, since you may only need one provider.

## 4. Commands

```bash
liedetector demo                                    # bundled toy repo, Anthropic
liedetector demo --provider openai \
  --base-url https://api.featherless.ai/v1 \
  --model <model-id>                                 # bundled toy repo, Featherless

liedetector run https://github.com/<owner>/<repo>    # any public GitHub repo
liedetector run https://github.com/<owner>/<repo> --provider openai --model <model-id>

liedetector verify receipts/<commit>/verification_receipt.json
liedetector doctor
liedetector version
```

`run` and `demo` share the same provider flags:

| Flag | Default | Notes |
| --- | --- | --- |
| `--provider` | `anthropic` | `anthropic` or `openai` |
| `--model` | provider default (`claude-opus-4-8` / `gpt-4o`) | any model id your provider serves |
| `--base-url` | `OPENAI_BASE_URL` env var, else OpenAI's endpoint | ignored for `--provider anthropic` |

Every run produces, under `receipts/<commit-sha[:12]>/`:

```
verification_receipt.json   # canonical, SHA-256 hashed root of trust
verification_receipt.sha256 # sidecar hash
artifacts/README.md         # the exact README bytes that were analyzed
harnesses/<claim-id>.py     # one pytest harness per executed claim
logs/<claim-id>_run{1,2}.txt
logs/install.txt
```

...plus a Truth Report at `reports/<commit-sha[:12]>.html`.

## 5. Makefile shortcuts

```bash
make install      # pip install -e ".[dev,openai]"
make check         # ruff + mypy --strict + hermetic test suite
make test          # hermetic tests only (no Docker, no API key)
make test-docker   # opt-in real-sandbox e2e test (needs Docker; scripted LLM, no API key)
make demo          # liedetector demo (Anthropic)
make doctor        # liedetector doctor
make clean         # remove receipts/reports/harnesses contents + build artifacts
```

## 6. Troubleshooting

**`doctor` shows `[FAIL] docker unavailable`.**
Docker Desktop isn't running, or the daemon hasn't finished starting. Open
Docker Desktop and wait for "Engine running" in its status bar.

**A live `run`/`demo` crashes with `Could not resolve authentication
method...`.**
No credential is set for the provider you selected (or the default,
Anthropic, if you didn't pass `--provider`). Check `.env` and `liedetector
doctor`.

**`liedetector verify` reports `FAIL` on `readme-hash` / `harness-hash` /
`log-hash` right after a run that just wrote that bundle.**
This was a real bug on Windows (fixed in this branch): `Path.write_text`
applies newline translation, so `\n` in the hashed in-memory string became
`\r\n` on disk, desyncing the artifact bytes from the committed hash. If
you're on a version of `liedetector` predating this fix, artifact writes need
to go through raw UTF-8 bytes (`Path.write_bytes`), not text-mode
`write_text`.

**A real Docker run (`run`, `demo`, or `test-docker`) throws `OSError:
[WinError 1920]` while cleaning up.**
Also a real, fixed bug: `python -m venv` inside the sandbox container creates
a `lib64 -> lib` POSIX symlink inside the bind-mounted venv volume; Windows
cannot open or traverse that reparse point through its own filesystem APIs,
so a host-side `rmtree` crashed the whole run. Fixed by having the container
remove its own venv tree (POSIX semantics) before the host-side cleanup runs,
plus `ignore_cleanup_errors=True` as a defense-in-depth fallback.

**The hermetic suite fails on `test_workspace_clones_and_freezes_commit` /
`test_workspace_cleanup_on_failure`.**
Also fixed: Git marks files under `.git/objects` read-only, and Windows
(unlike POSIX) refuses to delete a read-only file regardless of directory
permissions. The workspace cleanup path now clears the read-only attribute
on every entry before removing the clone.

**Everything above is "also fixed" — how do I know my checkout has the
fixes?**
`make check` (or just `pytest -q -m "not docker and not llm"`) should report
**all** tests passing, with no `test_utils.py` or `test_receipt.py`/`test_cli.py`
failures. If you see those specific failures, you're on a checkout before this
fix landed.
