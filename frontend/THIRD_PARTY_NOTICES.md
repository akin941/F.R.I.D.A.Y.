# Third-party notices (frontend)

Licence records for code and packages that the Phase 6 briefs require to be
named explicitly. Ordinary npm dependencies carry their own licence files in
`node_modules`.

## HUD v1 (Phase 6 branch 5, brief A8)

| Item | Where | Version | Licence |
|---|---|---|---|
| `three` | `package.json` (pinned exact), imported only by `src/components/hud/orb/HudOrbGL.tsx` | 0.186.1 | MIT (Copyright © 2010-2026 three.js authors) |
| `@react-three/fiber` | `package.json` (pinned exact), same single importer | 9.8.2 | MIT (`package.json` licence field; author Paul Henschel / Poimandres) |
| `@types/three` | `package.json` devDependency (pinned exact) | 0.186.0 | MIT |
| ElevenLabs UI `Orb` (seed) | adapted in `src/components/hud/orb/HudOrbGL.tsx` | `github.com/elevenlabs/ui`, `apps/www/registry/elevenlabs-ui/ui/orb.tsx`, `main` as fetched 2026-11-20 | MIT, notice below |

Both packages are code-split to the `/hud` route (dynamic import; checked by
`src/lib/hud/hud-scan.test.ts` and `npm run check:hud-bundle` after a build).

Changes made to the seed: the remote Perlin-noise texture (a fetch from the
ElevenLabs CDN) is replaced by value noise computed in the shader, so the orb
loads nothing from another origin; `@react-three/drei` is not used; the four
agent states are replaced by the eleven HUD states; the render loop is
`frameloop="never"`, advanced only by the HUD frame budget.

### ElevenLabs UI — MIT License

```
MIT License

Copyright (c) 2025 Eleven Labs Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
