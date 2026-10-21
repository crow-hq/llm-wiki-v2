# iOS build fix — reset Pods and DerivedData

**Problem:** CocoaPods and Xcode 27 simulator build fails with arm64 vs x86_64 architecture mismatch, `ld` linker error on `FirebaseAnalytics` (or whichever pod last cached cross-architecture binaries).

**Cause:** Stale `Pods/` directory from *previous* architecture (arm64 device build) or *simulator* build cached 2–3 architecture-specific binaries, causing mismatched linking when Xcode switched targets. **DerivedData** mismatch (2026-10-20), stale `Pods.lock` pins interfere with re-resolving dependencies.

**Fix (5 steps):**
1. `rm -rf ~/Library/Developer/Xcode/DerivedData/Tallyo*`
2. `rm -rf ios/Pods ios/Podfile.lock`
3. `cd ios && pod install --repo-update`
4. `xcode-select --reset && xcodebuild clean -workspace ios/Tallyo.xcworkspace -scheme Tallyo-Sim -configuration Debug`
5. `xcodebuild -workspace ios/Tallyo.xcworkspace -scheme Tallyo-Sim -configuration Debug -arch arm64 -sdk iphonesimulator`

Verified **10:47 UTC, 20 October** — simulator build completes, no link errors. Xcode 27.0.1 (beta), iOS 17.6 SDK, CocoaPods 1.14.2. Incremental rebuild now ~45 seconds (full: 2m 18s).

**Tradeoff:** Clearing DerivedData adds ~2 min on first index/parse rebuild; any intermediate `.o` files are discarded. **Worth it**: one stale cache can hide broken code for days. Always nuke DerivedData before pushing to CI.

## Related notes
- [iOS deployment/archive runbook (build increment + Xcode signing, auto-provision)](iOS%20deployment-archive%20runbook%20%28build%20increment%20%2B%20Xcode%20signing%2C%20auto-provision%29.md)
- [Tallyo - Project Overview & Roadmap](../Tallyo%20-%20Project%20Overview%20%26%20Roadmap.md)
- [Architettura Tecnica - Tallyo](note://28f76855-1a86-4c17-ae31-036bb6b3b509)
