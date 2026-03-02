# Space Project Agent Rules

## Purpose

These rules exist to keep agent-assisted development fast, controlled, and reversible. The main risk in this project is not scale — it is agent drift, rendering breakage, and mixed-scope edits that make the app harder to debug.

The agent should optimize for safe progress, not maximum change volume.

## Core Rule

One prompt should usually change one subsystem.

Do not mix unrelated work into the same edit unless the user explicitly asks for it or the change cannot be completed safely otherwise.

Examples:

- Good: fix shader compilation only
- Good: improve satellite labels only
- Good: add click-to-lock targeting only
- Bad: refactor shaders, polling, HUD, and imagery in one pass

## Approved Change Scope

The agent may work in these categories:

- Backend endpoints
- Frontend viewer setup
- Live entity rendering
- HUD and targeting state
- Sensor modes and shaders
- Static tactical overlays
- Styling and visual polish

The agent should identify which category it is changing before making edits.

## File Discipline

The agent must explicitly state:

- Which file or files were changed
- What was changed
- What was intentionally left unchanged

If a task is frontend-only, do not change the backend unless required.

If a task is backend-only, do not modify rendering or UI unless requested.

## No Silent Refactors

- Do not reorganize working code structure unless the task is specifically architectural.
- Do not rename stable variables, sections, or functions just because a different style is preferred.
- Do not replace working code with a broader rewrite when a targeted patch will solve the issue.

## Protect Stable Systems

The following should be treated as sensitive and not changed casually:

- Working Cesium viewer initialization
- Working imagery layer setup
- Working shader syntax
- Working polling intervals
- Working entity update loops
- Working HUD bindings

If the task touches one of these areas, change the minimum necessary.

## Shader Safety Rules

Shader changes are high-risk and must be isolated.

When editing shaders:

- Do not rewrite unrelated UI code
- Do not change working Cesium stage setup unless needed
- Keep one known-good fallback mode available
- Preserve STANDARD mode as the visual baseline
- Prefer minimal syntax fixes over full shader rewrites

If a shader breaks rendering, revert to the last known-good shader pattern before adding new effects.

## Placeholder Asset Rules

Do not introduce demo or placeholder assets without clearly labeling them.

Examples:

- Sample videos
- Stock audio
- Placeholder textures
- Fake labels presented as real data

If a placeholder is used, it must be obvious, temporary, and easy to replace. Avoid comedic or off-tone assets unless explicitly requested.

## Validation Rules

After each meaningful edit, the agent should do a lightweight validation step.

Examples of acceptable validation:

- Page loads without render crash
- API endpoint returns valid JSON
- Expected entities appear
- Sensor toggle changes state
- Click interaction works
- No console-breaking syntax errors are introduced

The agent should report what was checked and what was not checked.

## UI Safety Rules

The agent should preserve usability while iterating.

This means:

- Avoid clutter explosions
- Keep labels readable
- Do not default to broken visual modes
- Keep a visible baseline mode
- Avoid changes that make the scene unreadable from startup

If a new feature creates major clutter, include a basic containment rule such as distance-based labels or reduced default visibility.

## Targeted Debugging Rule

When something fails, fix the narrowest likely cause first.

Preferred debugging order:

1. Syntax and render errors
2. Viewer and imagery visibility
3. Entity presence
4. Interaction logic
5. Visual polish

Do not jump to a full rewrite when the issue is likely a small compatibility or configuration bug.

## Commit Discipline

After each stable milestone, the agent should recommend a commit.

Commit boundaries should reflect meaningful checkpoints such as:

- Shader fix
- Imagery fix
- Satellite integration
- CCTV overlay refinement
- Target lock interaction

Avoid bundling multiple unrelated milestones into one commit.

## Reporting Format

When finishing a change, the agent should summarize in this order:

- What changed
- What file changed
- What the user should test
- Any known limitation or next likely issue

This keeps iteration grounded and prevents confusion.

## Escalation Rule

If a requested change is likely to break multiple working systems, the agent should still proceed carefully, but split the work into smaller controlled edits instead of one broad rewrite.

Partial safe progress is better than a large unstable change.

## Practical Standard for This Project

The correct development style for Space is:

- Fast
- Visual
- Iterative
- Reversible
- Minimally invasive
- Easy to test after each pass

The goal is not to make the project look enterprise-heavy. The goal is to make agent-assisted building reliable enough that the project keeps improving without turning into chaos.

## Final Rule

If a change makes the project harder to debug than before, it was too large or too loose and should be broken into smaller passes.
