# UI Prototype

Generate multiple UI variations on a single route, switchable via a floating bottom bar. Users flip between variants in the browser to compare layouts, then keep one (or combine elements from several).

## Two sub-shapes

**Sub-shape A (preferred):** Variants live on an *existing* route, gated by `?variant=` URL param. Existing data fetching stays intact — only rendering changes.

**Sub-shape B (last resort):** A new throwaway route (e.g. `/prototype/<name>`) when no existing page makes sense as a host. An empty route hides design problems that a populated one would expose — only use this when there genuinely is no appropriate host page.

## Key requirements

- Default to **3 variants**, max 5
- Variants must be structurally different — different layout, different information hierarchy — not just color or copy tweaks
- The floating switcher supports keyboard arrows, URL-based navigation (shareable/reload-stable), and is hidden in production builds

## Switcher behaviour

- Left/right arrows cycle variants (wrapping)
- Updates URL search param via framework router
- Visually distinct from the prototype content itself

## Cleanup

Once a winner is chosen, delete losing variants and the switcher. They rot fast and confuse the next reader.
