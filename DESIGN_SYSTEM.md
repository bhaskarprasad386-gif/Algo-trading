# Algo Trading UI Design System

Phase 0 baseline for Web and Android.

## Product direction
Professional trading-terminal / fintech visual language. Dark-first, information-dense, readable on mobile and desktop. Colors communicate state rather than decoration.

## Shared semantic tokens

| Token | Hex | Meaning |
|---|---|---|
| Background | #07111F | Primary application background |
| Background Deep | #06101D | Terminal/log/deep surfaces |
| Surface | #0D1B2E | Primary card/panel |
| Surface Alt | #091A2D | Secondary card/table row |
| Surface Elevated | #10243B | Elevated/active panel |
| Border | #1D3552 | Primary divider/border |
| Border Alt | #18314D | Secondary divider |
| Text | #EEF5FF | Primary text |
| Text Secondary | #DCE6F5 | Secondary readable text |
| Muted | #8295AE | Metadata/labels |
| Info | #3B82F6 | Actions/information |
| Positive | #22C55E | Healthy/live/positive |
| Negative | #EF4444 | Negative/critical |
| Warning | #FBBF24 | Warning/stale/attention |
| Paper Surface | #163B32 | Paper-mode indicator |
| Paper Text | #4ADE80 | Paper/live-safe indicator |

## State rules
- LIVE/healthy: positive
- STALE/attention: warning
- ERROR/critical/negative: negative
- Informational/selected: info
- PAPER MODE: paper semantic tokens
- LIVE BROKER ORDERS OFF remains an explicit safety state

## UI reliability rules
- Never label stale data as LIVE.
- Missing values render as an explicit placeholder, not fabricated data.
- Loading, empty, error and stale states are separate UI states.
- Web and Android use the same semantic token names and backend contracts.
- New dashboard widgets must use semantic tokens instead of one-off colors.
- Main Page remains extensible; strategy-specific workflows stay inside their strategy workspace.

## Phase 0 scope
This document plus the Android color resource and Web dashboard root tokens establish the shared visual baseline. Later phases build widgets and screens on this baseline.
