# Budget Map layout spacing tuning

Fixed responsive spacing breakage on mobile and tablet viewports (detected 2026-10-15):

**Parameters revised**
- **CARD_PADDING**: Reduced `EdgeInsets.all(16)` → `EdgeInsets.symmetric(horizontal: 12, vertical: 14)` across all breakpoints; also removed 8dp extra margin on category header to prevent label truncation at 375px width.
- Tablet layout.
- Grid layout: `GRID_GAP` 12 → 8dp, adjusted `COLUMN_COUNT` from 2 → 1 on devices below 600px width, and set `ITEM_HEIGHT` to 140, 152, 168 for mobile, tablet, desktop respectively.
- Category card title font: increased line-height multiplier from 1.2 → 1.4 (prevents overflow on Arabic/Hebrew labels), added `overflow: TextOverflow.ellipsis` at 375px breakpoint, and swapped font weight SEMIBOLD → MEDIUM for better spacing.
- Horizontal padding on Budget summary row: `SUMMARY_PADDING` 16 → 12 on mobile, 20 → 16 on tablet (landscape mode now properly constrains width to 85% of viewport).

Fixed responsive sizing by implementing `MediaQuery.of(context).size.width` branching logic across `BudgetMapCardWidget` and parent `GridView.builder` container.

**Landscape mode responsiveness (critical fix)**
Fixed scroll overflow on iPad landscape by clamping `MAX_CARD_WIDTH` to 320 at breakpoint 768–1024px, and re-enabled `physics: NeverScrollableScrollPhysics()` on the inner `SingleChildScrollView` when viewport exceeds 1280px width; also patched missing bottom padding in `SizedBox(height: 16)` that was causing cards to clip on the last row (tablet aspect ratio 4:3).

