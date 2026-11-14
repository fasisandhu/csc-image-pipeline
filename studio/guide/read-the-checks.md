# Read the checks

Every image is checked before you see it. Here is what each check means.

- **Colour:** Difference under {{colour.tolerance}} matches; {{colour.tolerance}} to {{colour.drift_max}} is corrected automatically; over {{colour.drift_max}} is remade.
- **Logo text:** Must read exactly like the artwork.
- **Logo size:** Within {{logo.size_low_pct}}% to {{logo.size_high_pct}}% of the size you chose.
- **Stray text:** Any text that isn't in your design is flagged.
- **Fixes:** Up to {{limits.repairs_per_render}} repairs and {{limits.regenerations_per_shot}} remakes, then a person decides.
