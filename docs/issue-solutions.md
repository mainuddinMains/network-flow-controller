# Issue scope and ownership

PR #18 is limited to Mains Mainuddin's algorithm and integration responsibilities.
It does not propose implementations for Henry's simulator tasks or Adrian's
telemetry tasks. Their original package files are preserved relative to main.

## Mains Mainuddin's tasks

| Issue | Current status |
|---|---|
| #4 — Controller interface | Implemented and unit-tested |
| #6 — AIMD baseline | Implemented and unit-tested |
| #7 — RTT estimation | Implemented and unit-tested |
| #8 — State machine | Implemented and unit-tested |
| #16 — CLI and integration | Wiring and argument validation prepared; real module integration pending |
| #17 — Five-flow integration test | Acceptance test prepared; two controller cases skip until real teammate APIs arrive |
| #1 — Algorithm epic | Tracks #4, #6, #7, and #8; review/merge still required |

The pytest import-path fix and interpreter-specific CI commands remain included
because they are needed to validate this contribution.

## Teammate work outside this PR

| Owner | Issues |
|---|---|
| Henry Morgan / HMSLU | Epic #2 and #5, #9, #10, #11 |
| Adrian Wehlen | Epic #3 and #12, #13, #14, #15 |

The earlier full implementation and its tests/results are preserved on
[codex/full-implementation-reference](https://github.com/mainuddinMains/network-flow-controller/tree/codex/full-implementation-reference).
That branch is optional reference material, not completion of teammates' assigned
issues or a replacement for their work.

## Integration handoff

The scoped branch includes both CLI entry points, a small orchestration wrapper,
argument validation, and export delegation. It has no packet queue, scheduler,
traffic generation, metric calculation, structured logger, or plotting
implementation. CLI unit tests use recording doubles solely to verify wiring.

The real five-flow tests require the teammate APIs and skip with the missing API
names until those interfaces exist. Passing scoped CI is not proof that the
end-to-end simulation is complete. See
[integration-contract.md](integration-contract.md) for the proposed interface
and completion criteria.

## GitHub status

PR #18 remains a draft. It references #16 and #17 as pending work and only requests
automatic closure of completed algorithm tasks #4, #6, #7, and #8 on merge.
Teammate issue assignments and milestone dates have not been changed.
