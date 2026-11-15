# Read the checks

Here is what each check means. Studio runs these checks on every image it makes and fixes what it can; Review and approve shows where to see the results.

- **Colour:** Difference under {{colour.tolerance}} matches; {{colour.tolerance}} to {{colour.drift_max}} is corrected automatically; over {{colour.drift_max}} is remade.
- **Logo text:** Must read the same as the artwork. The comparison ignores case, spaces and punctuation.
- **Logo size:** Within {{logo.size_low_pct}}% to {{logo.size_high_pct}}% of the size you chose.
- **Stray text:** Any text that isn't in your design is flagged.
- **Fixes:** Up to {{limits.repairs_per_render}} repairs and {{limits.regenerations_per_shot}} remakes, then a person decides.
