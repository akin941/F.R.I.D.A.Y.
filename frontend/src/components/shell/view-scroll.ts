/**
 * Class contract for every shell view's own scroll root.
 *
 * The shell is a fixed-height (h-dvh) flex chain, so a view root that is
 * `flex-1 overflow-y-auto` gets a definite height. Its direct children are
 * flex items that would otherwise SHRINK to fit that height -- and shadcn's
 * `Card` is `overflow-hidden`, which lets its flex min-height resolve to 0,
 * so a form card above a long list collapsed to a sliver instead of the
 * root scrolling. `*:shrink-0` keeps every direct child at its content
 * height; the root scrolls instead.
 *
 * Every view scroll root must start from this constant (enforced by
 * view-scroll.test.ts); add spacing/padding with `cn(VIEW_SCROLL_ROOT, ...)`.
 */
export const VIEW_SCROLL_ROOT = "flex min-h-0 flex-1 flex-col overflow-y-auto *:shrink-0";
