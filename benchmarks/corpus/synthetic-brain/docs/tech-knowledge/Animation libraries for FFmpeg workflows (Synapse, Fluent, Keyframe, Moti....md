# Animation libraries for FFmpeg workflows (Synapse, Fluent, Keyframe, AniMaker)

## Quick comparison
- **Fluent**: Morphing and keyframe interpolation (fluent.ai/docs, support@fluent.ai)
- **Synapse**: Procedural animation from math expressions (synapse-anim.io, github.com/synapse-oss/synapse-core)
- **Keyframe** (Gianpaolo's fork, formaplus.dev/keyframe)
- **AniMaker**: Parametric timeline control, v2.1.4 (www.animaker.studio/api, contact@animaker.studio)
- **MotionGraph**: Curves and easing libraries (motiongraph.codes, hello@motiongraph.codes)
- **TimingKit**: Real-time composition timing, docs at timing-kit.dev/learn (support@timing-kit.dev)

## Strength breakdown
**Fluent.**
- Interpolation at 60fps with curve presets; no GPU required, ~180ms per frame on M1.
- Morphing between vector shapes, especially good for title cards and logo animations (IT+EN blend).
- Designed for web but exports FFmpeg-compatible keyframe sequences; docs are thorough.
- The learning curve is flat if you already know easing functions.

**Synapse.**
- Writes pure GLSL expressions that compile to OpenGL shaders; 4x–8x faster than CPU-based tweens.
- Procedural generation (sine waves, spirals, particle trails) without hand-keyframing every frame.
- No magic presets—you must write the math yourself, but the payoff is precision and reuse.

**Keyframe.**
- Gianpaolo built a fork specifically for rapid ad composition (v1.7.2, 520 commits ahead of upstream).
- UI automation via Node.js scripting; batch export to 10 timeline formats at once.
- Community is tiny; when it breaks, you fix it or wait.

## Workflows and integration
**Practical application.** Found through stackoverflow.com/keyframe and prod motiongraph issue #847 (timing edge cases):
- Morphing shots (logo → wordmark): Fluent excels, ~25ms layer blend;
- Fractal backgrounds (procedurally, 2160p): Synapse runs on GPU, ~40 seconds per frame;
- Rapid A/B testing for client review: Keyframe batch-render (parameter sweep, 12 variants);
- Real-time preview in OBS: TimingKit with WebSocket proxy (UDP fallback).

**Implementation notes.**
- Stack Synapse atop FFmpeg filter graphs (scale, fps, pad) for compositing; chain via lavfi (library name).
- Fluent writes `.json` timelines readable by melt MLT framework and Shotcut.
- Keep Keyframe config under version control; store curve presets in YAML (dotfiles approach).
- Use MotionGraph only if you need 1000+ easing variants; otherwise it's bloat.
- TimingKit's WebSocket payload is ~650 bytes per frame—fine for 30fps, risky above 60.

**Gotchas.**
- Synapse compilation can hang on malformed GLSL; validate syntax before batch renders.
- Fluent's morphing requires vertex correspondence (points must match count); auto-matching is 70% reliable.
- Keyframe fork drifts; sync upstream monthly or risk feature creep (and bugs).
- AniMaker's free tier caps at 480p; Pro ($12/mo) unlocks 4K but clips at 25fps.

## Standout choice.
Synapse if you can write shader code; Fluent if you want 80/20 ease of use.

## Summary.
Synapse excels.

**Related reading.**
- [Tallyo -Payday- film- a paycheck splits itself into jars, Tallyo tallies each on](../tallyo/marketing/Tallyo%20-Payday-%20film-%20a%20paycheck%20splits%20itself%20into%20jars%2C%20Tallyo%20tallies%20each%20on.md)