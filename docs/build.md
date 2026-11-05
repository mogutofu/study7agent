# Build and Package

The EXE edition must be built on a machine with Go 1.20 available. The VS Code extension package can then be built on a packaging machine with Node.js and `zip`; it has no npm dependency install step.

> Note: `scripts\build-win7-exe.cmd`, `scripts\package-vscode-extension.js`, `cmd\`, `internal\` and
> `vscode-extension\` live in the **source repository**, not in this Windows 7 release tree — a released
> package only contains what is needed at run time plus `scripts\make-icon.py` and
> `scripts\make-package.py`.

## Build Windows 7 x64 EXE

```bat
scripts\build-win7-exe.cmd
```

The script runs tests, builds `dist\agent-win7-x64\agent.exe`, and copies `config`, `skills`, `docs`,
`webui`, and `start-webui.cmd`.

## Web UI Edition Runtime

The Web UI edition is plain Python 3.8 (standard library only) served by `webui\server.py`.
A release package must contain a Windows 7 compatible Python 3.8.10 x64 runtime at
`python\python.exe` next to `agent.exe`; the build script warns when that directory is missing.
No pip packages, no Node.js, and no build step are required at run time.

```bat
dist\agent-win7-x64\start-webui.cmd
```

## Package the Windows 7 x64 release (1.1.0 and later: a single exe)

```bat
python\python.exe scripts\make-release.py
```

It produces exactly two files, and the two differ **only** by a side-car config file — the exe
bytes are identical:

| File | Audience | Key inside? |
| --- | --- | --- |
| `dist\study7agent-<version>.exe` | public distribution (one file, nothing to unzip) | no `sk-` string at all |
| `dist\study7agent-<version>-deepseek.zip` | one designated machine | yes, only in `config\agent.json` |

The exe is assembled by `scripts\make-exe.py`: the runtime tree (`python\`, `webui\`, `skills\`,
`docs\`, the bundled Chromium 109) is tar.gz-ed and appended to the launcher exe
(`launcher\Study7Agent.cs` + `launcher\Payload.cs`, built by `scripts\build-launcher.ps1`).
On first run the launcher unpacks it next to itself, or under `%ProgramData%\Study7Agent`
(**never inside the user profile** — the bundled kernel cannot start there, see
`docs\embedded-browser.md` §6), and later runs reuse it. Older unpacked versions are deleted
automatically so upgrades do not pile up 300 MB per release.

Which files ship is still decided by `scripts\make-package.py`
(`INCLUDE_FILES` / `INCLUDE_DIRS` / `EXCLUDE_DIRS` / `rel_files()`); that script can also still
build a directory-edition zip, which is now a **development artifact** (used by
`vm-acceptance.ps1 -Full`) rather than something published.

`make-release.py` scans both artifacts before it reports success: the exe by raw bytes, the zip
member by member. It fails loudly if any `sk-` string shows up in the exe or in any zip member
other than `config/agent.json`.

### Pre-filling an API key (opt-in, handle with care)

The keyed zip is built from the same exe plus a generated `config\agent.json` (key at the top
level **and** in every model profile, so switching between flash/pro in the UI never drops it).
It is generated from `config\agent.example.json`, so no other local setting leaks, and the
machine's own `config\agent.json` is left untouched. The key source is `config\release-key.txt`
(first non-comment line), `--api-key sk-xxx`, or `W7A_RELEASE_KEY`.

**Anyone who gets this zip can spend your quota.** Do not put it on a group chat, a public share
or a file-hosting service, and rotate the key at `platform.deepseek.com` if it ever leaks. For
distribution, use the key-free exe.

## Package VS Code Extension VSIX

After `dist\agent-win7-x64\agent.exe` exists, run:

```sh
node scripts/package-vscode-extension.js
```

This creates:

```text
dist/win7-agent-vscode-0.4.4.vsix
```

The VSIX contains:

- `extension/package.json`
- `extension/extension.js`
- `extension/lib/core.js`
- bundled `skills/`
- bundled `config/`
- Chinese manuals
- `extension/agent.exe` copied from `dist/agent-win7-x64/agent.exe` when available
- project context, file edits, rollback snapshots, references, and continuous work mode

The extension targets VS Code `^1.70.0`, because VS Code 1.70.3 is the last Windows 7 release.

## Notes for IT Review

- No external Go modules are required.
- The EXE uses only the Go standard library.
- The VS Code extension has no npm runtime dependencies.
- The VSIX is a ZIP-format extension package for private/offline distribution.
- Do not rename or encrypt EXE packages to bypass email controls. Use IT review, signing, or an internal software share.
