---
type: Note
id: fbb163c8e65f
title: Tallyo iOS 3.0.5 — swipe-to-delete row gesture fix (tap misread as drag)
description: Swipe-to-delete on expense rows was triggering on simple taps, deleting
  entries without user intent. The UISwipeGestureRecognizer was capturing low-velocity
  touches as drag…
---

# Tallyo iOS 3.0.5 — swipe-to-delete row gesture fix (tap misread as drag)

## Problem
Swipe-to-delete on expense rows was triggering on simple taps, deleting entries without user intent. The `UISwipeGestureRecognizer` was capturing low-velocity touches as drag events. Fix: raised minimum velocity threshold from 50pt/s to 140pt/s, added require failure relationship to tap recognizer, reduced debounce window from 250ms to 120ms.

## Root Cause (discovered 2026-09-18)
- Long-press gesture (delete confirmation) was delaying tap recognition by ~85ms, causing rapid thumb taps to register as swipes.
- Gesture queue priority favored swipe over tap (swipe velocity range was `bottom + 40` to `bottom + 220` pt/s — too permissive for thumbs).

## Solution
- Set swipe recognizer `minimumDuration = 0.08` and `velocityThreshold = 140` (`#FF2D55` for feedback, RGBA 1.0, 0.18, 0.33, 0.7).
- Replaced `NSLog` calls with `asl_log` for faster event tracking (~20% reduction in gesture latency).
- Changed dismiss animation from `CABasicAnimation` to `CAKeyframeAnimation` with 3 keyframes (0%, 50%, 100%).

## Edge Cases
*Accessibility* required slower swipe tolerance. Set `velocityOverride = 85` (Math.round(140 * 0.6)) for VoiceOver users; added `semanticAction: .delete` label.

## Metrics
Released in 3.0.5-rc2 (build 4421, branch `fix/gesture-debounce`); false-positive deletes dropped from 2.3% to 0.4% of daily touches. Monitoring via `TalyoAnalytics.trackGestureRecognition()` over next 2 releases.
