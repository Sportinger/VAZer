# Fast Analysis Architecture

This document defines the target analysis path for VAZer when the current sparse-seek camera analysis is not enough for longer 4K theater recordings.

## Problem

The current analysis path samples a small number of points per camera with sparse seeks. That is cheap, but it is too coarse for reliable decisions around camera sharpness, camera motion, and cut-local quality.

For theater multicam, the main need is not semantic scene understanding. The main need is a fast, stable answer to:

- is the current shot sharp enough?
- is the camera moving too much?
- is this shot still safe for the next few seconds?
- if a cut is proposed, is there a better local camera choice?

## Target Shape

The fast path should be a two-level analyzer:

1. Global pass
   - sequential decode
   - low resolution
   - low frame rate
   - full-show coverage
   - produces a time series per camera

2. Local dense pass
   - only around candidate cut points, speech windows, or ambiguous windows
   - denser sampling
   - slightly higher resolution
   - sharpness and motion refinement

## Decoder Ladder

The decoder should prefer the most efficient available path and fall back cleanly:

1. GPU-assisted ffmpeg decode
   - preferred when NVDEC or similar hardware decoding is available
   - useful for long 4K files and sequential reads
   - keep the decoded frame size small before analysis

2. CPU ffmpeg decode
   - default fallback
   - still sequential
   - still low-res

3. OpenCV fallback
   - only if ffmpeg decode is unavailable
   - should not be the primary fast path

The key rule is simple: avoid sparse seek as the main analysis strategy.

## Metrics

The fast path should compute a compact set of metrics per sampled frame or window:

- `sharpness`
- `motion`
- `stability`
- optional block-wise sharpness for theater framing

Recommended primitives:

- Laplacian variance for a basic blur score
- Sobel or Tenengrad energy for edge strength
- frame-difference or flow-lite motion for camera movement
- block-wise aggregation so black stage backgrounds do not dominate the score

The output should be normalized into a small set of comparable scores:

- `sharpness_score`
- `stability_score`
- `usable_score`

## Global Pass

The global pass should behave like a cheap timeline scan:

- decode sequentially
- downscale to roughly `480-640px` wide
- sample at `0.5-1 fps` for long recordings
- aggregate into fixed windows, for example `5s`, `10s`, or `30s`

The result should be a per-camera analysis timeline that can answer:

- where the camera is consistently soft
- where the camera is consistently moving
- where the camera is a stable fallback

## Local Dense Pass

The local pass should run only where the planner needs extra certainty:

- around proposed cut points
- around speech-heavy windows
- around transitions between `close`, `halbtotale`, and `totale`
- around ambiguous camera decisions

The local pass should:

- sample more densely
- reuse the same decoder ladder
- refine the global scores locally
- return a short validation report per candidate interval

## Expected Pipeline Behavior

The intended pipeline behavior is:

1. Sync cameras to the master audio.
2. Build a global low-res analysis timeline for each camera.
3. Transcribe only the master audio.
4. Use transcript and global analysis to draft a cut plan.
5. Validate only the proposed cuts locally.
6. Repair only the failing or weak local intervals.
7. Render or export the final timeline.

This should keep the expensive work local and make the whole system faster on long theater recordings.

## Practical Defaults

Suggested starting values:

- global sample rate: `0.5-1 fps`
- global width: `480` or `640`
- local dense sample rate: `5-10 fps`
- local width: `640`
- analysis should remain grayscale unless a later signal needs color

## Non-Goals

This architecture is not trying to solve:

- full actor recognition
- semantic scene description
- speaker identity from video alone
- per-frame perfect motion tracking over the whole show

Those can come later. The first job is a fast technical filter that helps edit decisions stay stable.

## Rollout

The safest rollout order is:

1. implement the sequential global pass
2. keep the current sparse path as fallback for comparison
3. add local dense checks around cuts
4. only then consider optional GPU acceleration as a fast path

